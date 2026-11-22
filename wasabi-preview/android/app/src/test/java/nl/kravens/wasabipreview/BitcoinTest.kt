package nl.kravens.wasabipreview

import nl.kravens.wasabipreview.Bitcoin.Network
import nl.kravens.wasabipreview.Bitcoin.ParseError
import nl.kravens.wasabipreview.Bitcoin.Payment
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

// The iOS app's Bitcoin.selfCheck(), case for case: money logic and address parsing must agree on both platforms.
class BitcoinTest {
    private fun coin(sats: Long, score: Double, tx: String = "", index: Int = 0, confirmed: Boolean = true) =
        Coin(tx, index, sats, score, confirmed)

    private fun fails(text: String, expected: ParseError, network: Network? = null): Boolean =
        try { Bitcoin.parse(text, network); false } catch (e: ParseError) { e == expected }

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    private fun seg(s: String, hrp: String, version: Int, program: String): Boolean {
        val d = Bitcoin.segwit(s) ?: return false
        return d.hrp == hrp && d.version == version && hex(d.program) == program
    }

    private val v0 = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    private val v1 = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    private val v2 = "bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs"
    private val tb = "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7"
    private val legacy = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"

    @Test fun coinSelection() {
        val wallet = listOf(coin(500, 1.0), coin(300, 10.0), coin(200, 10.0), coin(100, 4.0), coin(900, 10.0, confirmed = false))
        var pick = Bitcoin.pickCoins(wallet, 10, 450)
        assertTrue(pick.coins.map { it.amount } == listOf(300L, 200L) && !pick.toxic) // private first, largest first, unconfirmed never
        pick = Bitcoin.pickCoins(wallet, 10, 550)
        assertTrue(pick.coins.map { it.amount } == listOf(300L, 200L, 100L) && !pick.toxic)
        pick = Bitcoin.pickCoins(wallet, 10, 700)
        assertTrue(pick.coins.map { it.amount } == listOf(300L, 200L, 100L, 500L) && pick.toxic) // reaching a non-private coin flags the merge
        pick = Bitcoin.pickCoins(wallet, 10, 700, onlyPrivate = true)
        assertTrue(pick.coins.map { it.amount } == listOf(300L, 200L) && !pick.toxic) // short, never toxic
        val siblings = listOf(coin(100, 5.0, "a", 2), coin(100, 5.0, "a", 0), coin(100, 5.0, "b"), coin(200, 5.0, "a", 1))
        assertEquals(listOf(0, 2), Bitcoin.ownEqualOutputs(siblings[0], siblings).map { it.index })
    }

    @Test fun privacyPercentage() {
        assertEquals(100, Mirror.privacyPercentage(listOf(coin(100, 50.0)), 50))
        assertEquals(0, Mirror.privacyPercentage(listOf(coin(100, 1.0)), 50))
        assertEquals(50, Mirror.privacyPercentage(listOf(coin(100, 25.5)), 50)) // semi-private counts partly, as in Wasabi
        assertEquals(50, Mirror.privacyPercentage(listOf(coin(100, 50.0), coin(100, 1.0)), 50))
    }

    @Test fun serverRules() {
        assertTrue(Server(host = "100.101.102.103").isTailscale && Server(host = "mac.example.ts.net").isTailscale)
        assertTrue(!Server(host = "192.168.1.20").isTailscale && !Server(host = "100.128.0.1").isTailscale && !Server(host = "100.64.1").isTailscale)
        val onion = "a".repeat(56) + ".onion"
        assertTrue(Server(transport = Transport.ONION, onion = onion, onionKey = "KEY").isAllowed)
        assertEquals("a".repeat(56) + ":descriptor:x25519:KEY", Server(transport = Transport.ONION, onion = onion, onionKey = "x:descriptor:x25519:KEY\n").authLine)
        assertTrue(!Server(transport = Transport.ONION, onion = "100.101.102.103").isAllowed && !Server(transport = Transport.TAILSCALE, host = onion).isAllowed)
    }

    @Test fun chainsAndFees() {
        fun tx(time: Long, round: Boolean) = HistoryItem(Instant.ofEpochSecond(time), "1", -1, null, time.toString(), round)
        val chains = HistoryItem.chains(listOf(tx(1, true), tx(5, true), tx(3, false), tx(4, true), tx(2, true), tx(6, false)))
        assertEquals(listOf(listOf("6"), listOf("5", "4"), listOf("3"), listOf("2", "1")), chains.map { c -> c.map { it.tx } })
        assertEquals(listOf("2", "6", "144"), Bitcoin.feeChoices(mapOf("2" to 9.0, "3" to 8.0, "6" to 5.0, "12" to 4.0, "144" to 2.0, "1008" to 1.0)))
        assertEquals(listOf("2"), Bitcoin.feeChoices(mapOf("2" to 1.0)))
        assertTrue(Bitcoin.feeChoices(emptyMap()).isEmpty())
        assertEquals(listOf("2", "144"), Bitcoin.feeChoices(mapOf("2" to 3.0, "6" to 3.0, "144" to 1.0))) // same rate as a faster target: dropped
    }

    @Test fun amounts() {
        assertEquals(100_000L, Bitcoin.sats("0.001"))
        assertEquals(100_000_000L, Bitcoin.sats("1"))
        assertEquals(1L, Bitcoin.sats("0.00000001"))
        assertNull(Bitcoin.sats("99999999999")) // above 21M BTC: rejected instead of overflowing
        assertEquals(10_000_000L, Bitcoin.sats("0.1"))
        assertEquals(2_100_000_000_000_001L, Bitcoin.sats("21000000.00000001"))
        assertNull(Bitcoin.sats("0.000000001"))
        assertTrue(Bitcoin.sats("abc") == null && Bitcoin.sats("") == null && Bitcoin.sats("1.2.3") == null)
        assertTrue(Bitcoin.btc(123_456_789) == "1.23456789" && Bitcoin.btc(-1) == "-0.00000001")
    }

    @Test fun bip21() {
        assertEquals(Payment(v0, 123_000, "Café"), Bitcoin.parse("BITCOIN:BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4?amount=0.00123&label=Caf%C3%A9&foo=bar"))
        assertEquals(Payment(legacy), Bitcoin.parse(" $legacy "))
        assertTrue(fails("hello", ParseError.NotBitcoin) && fails("", ParseError.NotBitcoin))
        assertEquals(Payment(v0, 123_000), Bitcoin.parse(Bitcoin.uri(v0, 123_000)))
    }

    @Test fun bip321() {
        assertTrue(fails("bitcoin:175tWpb8K1S7NmH4Zx6rewF9WQrcZv245W", ParseError.Invalid))
        assertEquals(Payment(legacy, null, "Luke-Jr"), Bitcoin.parse("bitcoin:$legacy?label=Luke-Jr"))
        assertEquals(2_030_000_000L, Bitcoin.parse("bitcoin:$legacy?amount=20.3&label=Luke-Jr").sats)
        assertEquals(Payment(legacy, 5_000_000_000, "Luke-Jr", "Donation for project xyz"), Bitcoin.parse("bitcoin:$legacy?amount=50&label=Luke-Jr&message=Donation%20for%20project%20xyz"))
        assertEquals(legacy, Bitcoin.parse("bitcoin:$legacy?lightning=lnbc420bogusinvoice").address)
        assertEquals(legacy, Bitcoin.parse("bitcoin:$legacy?sp=sp1qsilentpayment").address)
        assertTrue(fails("bitcoin:?lightning=lnbc420bogusinvoice", ParseError.OffChain(listOf("lightning"))))
        assertTrue(fails("bitcoin:?lno=lno1bogusoffer", ParseError.OffChain(listOf("lno"))))
        assertTrue(fails("bitcoin:?lno=lno1bogusoffer&sp=sp1qsilentpayment", ParseError.OffChain(listOf("lno", "sp"))))
        assertTrue(fails("bitcoin:?sp=sp1qsilentpayment", ParseError.OffChain(listOf("sp"))))
        assertTrue(fails("bitcoin:$legacy?req-somethingyoudontunderstand=50&req-somethingelseyoudontget=999", ParseError.Required("somethingyoudontunderstand")))
        assertEquals(legacy, Bitcoin.parse("bitcoin:$legacy?somethingyoudontunderstand=50&somethingelseyoudontget=999").address)
        assertEquals(v0, Bitcoin.parse("bitcoin:?bc=$v0&bc=$v1").address)
        assertEquals(v0, Bitcoin.parse("BITCOIN:${v0.uppercase()}?BC=${v1.uppercase()}").address)
        assertEquals(v0, Bitcoin.parse("BITCOIN:?BC=${v0.uppercase()}&BC=${v1.uppercase()}").address)
        assertEquals(tb, Bitcoin.parse("bitcoin:?tb=$tb", Network.TEST).address)
        assertTrue(fails("bitcoin:?tb=$tb", ParseError.WrongNetwork(Network.TEST, Network.MAIN), Network.MAIN))
        assertTrue(fails("bitcoin:$legacy?label=Luke-Jr&label=Matt", ParseError.Duplicate("label")))
        assertTrue(fails("bitcoin:$legacy?amount=42&amount=10", ParseError.Duplicate("amount")))
        assertTrue(fails("bitcoin:$legacy?amount=42&amount=42", ParseError.Duplicate("amount")))
        assertTrue(fails("bitcoin:$legacy?pop=callback%3a&req-pop=callback%3a", ParseError.Required("pop")))
        assertTrue(fails("bitcoin:?bc=$tb", ParseError.Invalid))
        assertEquals(legacy, Bitcoin.parse("bitcoin:$legacy?pop=callback%3a").address)
        assertTrue(fails("bitcoin:", ParseError.Empty) && fails("bitcoin:?foo=bar", ParseError.Empty))
        assertEquals(v0, Bitcoin.parse("bitcoin:?bc=$v2&bc=$v0").address)
        assertTrue(fails(v2, ParseError.Unsupported(2)) && fails("bitcoin:?bc=$v2", ParseError.Unsupported(2)))
        assertEquals(v0, Bitcoin.parse("bitcoin:$v0?pj=https%3A%2F%2Fexample.com&pjos=0").address)
        assertTrue(fails("bitcoin:$legacy?amount=50,000.00", ParseError.Amount) && fails("bitcoin:$legacy?amount=-1", ParseError.Amount) && fails("bitcoin:$legacy?amount=1e3", ParseError.Amount))
        assertTrue(fails("bitcoin:$legacy?amount=0.000000001", ParseError.Amount) && fails("bitcoin:$legacy?amount=21000001", ParseError.Amount) && fails("bitcoin:$legacy?amount=", ParseError.Amount))
        assertTrue(Bitcoin.parse("bitcoin:$legacy?amount=.5").sats == 50_000_000L && Bitcoin.parse("bitcoin:$legacy?amount=5.").sats == 500_000_000L)
    }

    @Test fun segwitVectors() {
        assertTrue(seg("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "bc", 0, "751e76e8199196d454941c45d1b3a323f1433bd6"))
        assertTrue(seg(tb, "tb", 0, "1863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262"))
        assertTrue(seg("bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y", "bc", 1, "751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6"))
        assertTrue(seg("BC1SW50QGDZ25J", "bc", 16, "751e"))
        assertTrue(seg(v2, "bc", 2, "751e76e8199196d454941c45d1b3a323"))
        assertTrue(seg("tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy", "tb", 0, "000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"))
        assertTrue(seg("tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c", "tb", 1, "000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"))
        assertTrue(seg(v1, "bc", 1, "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"))
        for (bad in listOf(
            "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut", "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
            "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf", "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",
            "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh", "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",
            "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4", "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R",
            "bc1pw5dgrnzv", "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav", "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",
            "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq", "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",
            "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j", "bc1gmk9yu", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5",
            "bc1rw5uspcuh", "bc10w508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kw5rljs90",
        )) assertNull(bad, Bitcoin.segwit(bad))
        for (good in listOf("A12UEL5L", "a12uel5l", "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw", "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w", "?1ezyfcl",
            "an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs")) assertEquals(good, false, Bitcoin.bech32(good)?.m)
        for (good in listOf("A1LQFN3A", "a1lqfn3a", "abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx", "split1checkupstagehandshakeupstreamerranterredcaperredlc445v", "?1v759aa",
            "an83characterlonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11sg7hg6")) assertEquals(good, true, Bitcoin.bech32(good)?.m)
        for (bad in listOf(" 1nwldj5", "\u007F1axkwrx", "\u00801eym55h", "an84characterslonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1569pvx",
            "pzry9x0s0muk", "1pzry9x0s0muk", "x1b4n0q5v", "li1dgmt3", "de1lg7wtÿ", "A1G7SGD8", "10a06t8", "1qzzfhee",
            "qyrz8wqd2c9m", "1qyrz8wqd2c9m", "y1b0jsk6g", "lt1igcx5c0", "in1muywd", "mm1crxm3i", "au1s5cgom", "M1VUXWEZ", "16plkw9", "1p2gdwpf")) assertNull(bad, Bitcoin.bech32(bad))
    }

    @Test fun base58AndNetworks() {
        assertTrue(Bitcoin.isAddress(legacy) && Bitcoin.isAddress("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy") && Bitcoin.isAddress("1111111111111111111114oLvT2"))
        assertTrue(Bitcoin.isAddress("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", Network.TEST) && Bitcoin.isAddress("2MzQwSSnBHWHqSAqtTVQ6v47XtaisrJa1Vc", Network.REGTEST))
        assertTrue(fails("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3", ParseError.Invalid) && fails("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLz", ParseError.Invalid))
        assertTrue(fails(legacy, ParseError.WrongNetwork(Network.MAIN, Network.TEST), Network.TEST))
        assertTrue(fails("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", ParseError.WrongNetwork(Network.TEST, Network.MAIN), Network.MAIN))
        assertTrue(fails(tb, ParseError.WrongNetwork(Network.TEST, Network.REGTEST), Network.REGTEST))
        val bcrt = "bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"
        assertTrue(Bitcoin.isAddress(bcrt, Network.REGTEST) && fails(bcrt, ParseError.WrongNetwork(Network.REGTEST, Network.MAIN), Network.MAIN))
        assertTrue(Network.wasabi("Main") == Network.MAIN && Network.wasabi("TestNet4") == Network.TEST && Network.wasabi("Signet") == Network.TEST && Network.wasabi("RegTest") == Network.REGTEST)
        assertEquals(v0, Bitcoin.address("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"))
        assertTrue(fails(v0 + "x", ParseError.Invalid) && fails("bc1", ParseError.Invalid))
        assertFalse(Bitcoin.isAddress("bc1"))
    }

    @Test fun rawTransaction() {
        val tx = "01000000" + "01" + "00".repeat(31) + "01" + "00000000" + "00" + "ffffffff" +
            "02" + "e803000000000000" + "00" + "d007000000000000" + "00" + "00000000"
        val parsed = Bitcoin.inspect(tx)
        assertEquals(3000L, parsed?.outputs)
        assertEquals(listOf("01" + "00".repeat(31) + ":0"), parsed?.inputs)
        assertNull(Bitcoin.inspect("0100"))
    }
}
