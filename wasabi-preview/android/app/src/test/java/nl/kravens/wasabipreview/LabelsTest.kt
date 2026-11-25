package nl.kravens.wasabipreview

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

// Same cases as the iOS Bitcoin.selfCheck: desktop Wasabi's label ranking, suggestions and addresses awaiting payment.
class LabelsTest {
    private val keys = listOf(
        WalletKey("84'/0'/0'/0/1", false, 2, "Bob", "a"),
        WalletKey("84'/0'/0'/0/2", false, 0, "alice, Concert tickets", "b"),
        WalletKey("84'/0'/0'/0/3", false, 0, "", "c"),
        WalletKey("84'/0'/0'/1/9", true, 2, "Zed", "d"),
    )

    @Test fun rankedByReceiveUseAndRecency() = assertEquals(listOf("alice", "Concert tickets", "Bob", "Zed"), Labels.ranked(keys))

    @Test fun suggestionsMatchTypedTextAndSkipChosen() =
        assertEquals(listOf("Concert tickets"), Labels.suggestions(Labels.ranked(keys), "c", listOf("Alice")))

    @Test fun waitingAreCleanLabelledReceiveKeys() = assertEquals(listOf("b"), Labels.waiting(keys).map { it.address })

    @Test fun splitTrimsAndDropsEmpty() = assertEquals(listOf("Alice", "rent"), Labels.split(" Alice ,, rent "))

    @Test fun taprootFromAddress() {
        assertTrue(Bitcoin.isTaproot(Mirror.DEMO_TAPROOT))
        assertFalse(Bitcoin.isTaproot(Mirror.DEMO_ADDRESS))
    }
}
