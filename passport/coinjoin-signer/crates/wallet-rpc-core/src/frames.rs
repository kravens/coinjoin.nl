// SPDX-FileCopyrightText: 2026 Kevin Ravensberg <kevinravensberg@proton.me>
// SPDX-License-Identifier: MIT OR GPL-3.0-or-later

//! Multi-report framing for the wallet RPC USB HID transport.
//!
//! HID reports are 64 bytes. Frames larger than one report are split:
//!
//! - Init report:         `[0x00][frame_len u32 LE][data ...59 bytes]`
//! - Continuation report: `[0x01][seq u16 LE][data ...61 bytes]`
//!
//! v1 used a 16-bit length and a 7-bit sequence number, which capped a frame at
//! ~8 KiB — fine for the mock, far under a real coinjoin PSBT. The leading type
//! byte keeps init and continuation reports distinguishable no matter how large
//! the sequence number grows (a bare 16-bit counter would collide with the init
//! marker every 256 reports).
//!
//! A single host connection is assumed (no channels — unlike CTAPHID). A new
//! init report always resets reassembly, so a lost continuation can't wedge
//! the transport.

pub const REPORT_LEN: usize = 64;
const INIT_MARKER: u8 = 0x00;
const CONT_MARKER: u8 = 0x01;
const INIT_HEADER_LEN: usize = 5;
const CONT_HEADER_LEN: usize = 3;
const INIT_DATA_LEN: usize = REPORT_LEN - INIT_HEADER_LEN;
const CONT_DATA_LEN: usize = REPORT_LEN - CONT_HEADER_LEN;

/// Largest frame the device will reassemble: the biggest PSBT it accepts plus
/// room for the request header. Refusing longer frames at the framing layer
/// means a hostile length prefix never turns into an allocation.
pub const MAX_FRAME_LEN: usize = crate::protocol::MAX_PSBT_LEN + 1024;

/// Split a frame into HID reports, each exactly `REPORT_LEN` bytes (zero padded).
pub fn split_frame(frame: &[u8]) -> Vec<[u8; REPORT_LEN]> {
    let mut reports = Vec::new();

    let mut report = [0u8; REPORT_LEN];
    report[0] = INIT_MARKER;
    report[1..5].copy_from_slice(&(frame.len() as u32).to_le_bytes());
    let first = frame.len().min(INIT_DATA_LEN);
    report[INIT_HEADER_LEN..INIT_HEADER_LEN + first].copy_from_slice(&frame[..first]);
    reports.push(report);

    let mut offset = first;
    let mut seq = 1u16;
    while offset < frame.len() {
        let mut report = [0u8; REPORT_LEN];
        report[0] = CONT_MARKER;
        report[1..3].copy_from_slice(&seq.to_le_bytes());
        let chunk = (frame.len() - offset).min(CONT_DATA_LEN);
        report[CONT_HEADER_LEN..CONT_HEADER_LEN + chunk]
            .copy_from_slice(&frame[offset..offset + chunk]);
        reports.push(report);
        offset += chunk;
        seq += 1;
    }

    reports
}

/// Streaming reassembler for received reports.
#[derive(Default)]
pub struct Reassembler {
    expected_len: usize,
    next_seq: u16,
    buf: Vec<u8>,
}

impl Reassembler {
    /// Feed one report; returns the completed frame when the last chunk arrives.
    /// Malformed sequences reset state and return `None`.
    pub fn push_report(&mut self, report: &[u8]) -> Option<Vec<u8>> {
        match report.first() {
            Some(&INIT_MARKER) => {
                if report.len() < INIT_HEADER_LEN {
                    self.reset();
                    return None;
                }
                let len = u32::from_le_bytes(report[1..5].try_into().unwrap()) as usize;
                if len > MAX_FRAME_LEN {
                    self.reset();
                    return None;
                }
                self.expected_len = len;
                self.next_seq = 1;
                self.buf.clear();
                self.buf.reserve(len);
                let end = report.len().min(INIT_HEADER_LEN + len);
                self.buf.extend_from_slice(&report[INIT_HEADER_LEN..end]);
            }
            Some(&CONT_MARKER) => {
                if report.len() < CONT_HEADER_LEN || self.expected_len == 0 {
                    self.reset();
                    return None;
                }
                let seq = u16::from_le_bytes(report[1..3].try_into().unwrap());
                if seq != self.next_seq {
                    // Out of order — drop everything rather than splice a hole.
                    self.reset();
                    return None;
                }
                self.next_seq = self.next_seq.wrapping_add(1);
                let remaining = self.expected_len - self.buf.len();
                let end = report.len().min(CONT_HEADER_LEN + remaining);
                self.buf.extend_from_slice(&report[CONT_HEADER_LEN..end]);
            }
            _ => {
                self.reset();
                return None;
            }
        }

        if self.buf.len() >= self.expected_len {
            let frame = core::mem::take(&mut self.buf);
            self.reset();
            Some(frame)
        } else {
            None
        }
    }

    fn reset(&mut self) {
        self.expected_len = 0;
        self.next_seq = 0;
        self.buf = Vec::new();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roundtrip(len: usize) {
        let frame: Vec<u8> = (0..len).map(|i| (i % 251) as u8).collect();
        let reports = split_frame(&frame);
        let mut re = Reassembler::default();
        let mut out = None;
        for (i, report) in reports.iter().enumerate() {
            let res = re.push_report(report);
            if i + 1 < reports.len() {
                assert!(res.is_none(), "early completion at report {i}");
            } else {
                out = res;
            }
        }
        assert_eq!(out.expect("frame not completed"), frame);
    }

    #[test]
    fn single_report_frame() {
        roundtrip(10)
    }

    #[test]
    fn exact_init_capacity() {
        roundtrip(INIT_DATA_LEN)
    }

    #[test]
    fn two_reports() {
        roundtrip(INIT_DATA_LEN + 1)
    }

    #[test]
    fn psbt_sized_frame() {
        roundtrip(4000)
    }

    /// Past the old 8 KiB v1 ceiling, and past the 256-report point where a
    /// bare sequence byte would have collided with the init marker.
    #[test]
    fn real_coinjoin_psbt_sized_frame() {
        roundtrip(200_000)
    }

    #[test]
    fn max_frame() {
        roundtrip(MAX_FRAME_LEN)
    }

    #[test]
    fn oversize_length_rejected() {
        let mut re = Reassembler::default();
        let mut report = [0u8; REPORT_LEN];
        report[0] = INIT_MARKER;
        report[1..5].copy_from_slice(&((MAX_FRAME_LEN + 1) as u32).to_le_bytes());
        assert!(re.push_report(&report).is_none());
    }

    #[test]
    fn empty_frame() {
        let reports = split_frame(&[]);
        assert_eq!(reports.len(), 1);
        let mut re = Reassembler::default();
        assert_eq!(re.push_report(&reports[0]), Some(vec![]));
    }

    #[test]
    fn out_of_order_cont_resets() {
        let frame: Vec<u8> = vec![7; 200];
        let reports = split_frame(&frame);
        let mut re = Reassembler::default();
        assert!(re.push_report(&reports[0]).is_none());
        assert!(re.push_report(&reports[2]).is_none()); // skipped seq 1
                                                        // subsequent valid transfer still works
        roundtrip(200);
    }

    #[test]
    fn cont_without_init_ignored() {
        let mut re = Reassembler::default();
        let mut report = [0u8; REPORT_LEN];
        report[0] = CONT_MARKER;
        report[1..3].copy_from_slice(&1u16.to_le_bytes());
        assert!(re.push_report(&report).is_none());
    }

    #[test]
    fn unknown_marker_ignored() {
        let mut re = Reassembler::default();
        let mut report = [0u8; REPORT_LEN];
        report[0] = 0x42;
        assert!(re.push_report(&report).is_none());
    }

    #[test]
    fn init_resets_partial_frame() {
        let frame: Vec<u8> = vec![9; 200];
        let reports = split_frame(&frame);
        let mut re = Reassembler::default();
        assert!(re.push_report(&reports[0]).is_none());
        // new init mid-transfer wins
        let small = split_frame(&[1, 2, 3]);
        assert_eq!(re.push_report(&small[0]), Some(vec![1, 2, 3]));
    }
}
