// SPDX-FileCopyrightText: 2026 Kevin Ravensberg <kevinravensberg@proton.me>
// SPDX-License-Identifier: MIT OR GPL-3.0-or-later

//! Does `os/usbdev` answer a third-party app?
//!
//! Three read-only queries, each sent with `try_send_blocking_scalar` so a
//! refusal comes back as an error instead of a panic. What we learn:
//!
//! - all three answer → the server is reachable from an app, and the real USB
//!   transport is a vendoring job, not a Foundation blocker;
//! - errors → app USB access is genuinely gated, and the round transport has to
//!   wait for Foundation's shared vendor interface (or ship as a system
//!   service in the signed image).

pub mod messages {
    #[derive(Debug, Clone, server::Message)]
    #[response(usize)]
    pub struct NumInterfaces;

    #[derive(Debug, Clone, server::Message)]
    #[response(bool)]
    pub struct IsDeviceEmulationEnabled;

    #[derive(Debug, Clone, server::Message)]
    #[response(bool)]
    pub struct IsCableConnected;
}

use server::{CheckedConn, CheckedPermissions, MessageAllowed};

use crate::messages::*;

#[macro_export]
macro_rules! use_api {
    () => {
        mod usbdev_probe_permissions {
            use usbdev_probe::messages::*;
            #[derive(Clone, Default, server::Permissions)]
            #[server_name = "os/usbdev"]
            pub struct UsbDevProbePermissions;
        }
        type UsbDevProbe = usbdev_probe::UsbDevProbe<usbdev_probe_permissions::UsbDevProbePermissions>;
    };
}

#[derive(Default)]
pub struct UsbDevProbe<P: CheckedPermissions> {
    conn: CheckedConn<P>,
}

impl<P: CheckedPermissions> UsbDevProbe<P> {
    /// Connect only if the server exists, giving up after `timeout`.
    ///
    /// The default constructor blocks until the name resolves, which never
    /// happens in the simulator: it runs no `os/usbdev` at all, so an app there
    /// spins in the nameserver forever. `None` means "no USB server here",
    /// which is a different answer from "the server refused you".
    pub fn try_connect(timeout: std::time::Duration) -> Option<Self> {
        CheckedConn::try_connect_with_timeout(timeout).map(|conn| Self { conn })
    }

    pub fn num_interfaces(&self) -> Result<usize, xous::Error>
    where
        P: MessageAllowed<NumInterfaces>,
    {
        self.conn.try_send_blocking_scalar(NumInterfaces)
    }

    pub fn device_emulation_enabled(&self) -> Result<bool, xous::Error>
    where
        P: MessageAllowed<IsDeviceEmulationEnabled>,
    {
        self.conn.try_send_blocking_scalar(IsDeviceEmulationEnabled)
    }

    pub fn cable_connected(&self) -> Result<bool, xous::Error>
    where
        P: MessageAllowed<IsCableConnected>,
    {
        self.conn.try_send_blocking_scalar(IsCableConnected)
    }
}
