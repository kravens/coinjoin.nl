import XCTest

// The demo wallet a reviewer reaches without a computer, and the decoy that opens with it. Needs an empty Keychain
// (a fresh simulator), so leaving the demo asks for no Face ID.
final class DemoTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    func testDemoButtonShowsSampleWalletAndLeaves() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "connection"]
        app.launchArguments = ["-decoy", "NO"]
        app.launch()
        app.buttons["Try the demo wallet"].tap()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))

        app.buttons["Menu"].tap()
        app.buttons["menu.connection"].tap()
        app.buttons["Leave the demo wallet"].tap()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForNonExistence(timeout: 5))
    }

    // A wide screen (here a phone on its side, like iPhone Duo unfolded) shows balance and transactions side by side.
    // The coins are open and the right column is scrolled, so the screenshot shows whether both columns get the frosted
    // top bar.
    func testWideHomeShowsBalanceAndTransactionsTogether() throws {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "coins", "WASABI_DEMO": "1", "WASABI_DISCREET": "0"]
        app.launch()
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["TRANSACTIONS"].isHittable)
        XCTAssertTrue(app.staticTexts["Balance Daily"].isHittable)
        let right = app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.7))
        right.press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.45)))
        let shots = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("build/shots")
        try FileManager.default.createDirectory(at: shots, withIntermediateDirectories: true)
        try XCUIScreen.main.screenshot().pngRepresentation.write(to: shots.appendingPathComponent("wide-home.png"))
    }

    // Switching wallet inside the demo shows that wallet's own made-up data, and switching back restores Daily's.
    func testDemoWalletSwitch() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "home", "WASABI_DEMO": "1"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
        app.buttons["Wallet Daily, switch wallet"].tap()
        app.buttons["Savings"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Balance Savings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Year-end bonus"].waitForExistence(timeout: 5))
        app.buttons["Wallet Savings, switch wallet"].tap()
        app.buttons["Daily"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Groceries"].waitForExistence(timeout: 5))
    }

    // Receive offers earlier labels as chips, lists addresses awaiting payment, and reopens one of them as its request.
    func testReceiveSuggestsLabelsAndShowsWaitingAddresses() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "receive", "WASABI_DEMO": "1"]
        app.launch()
        XCTAssertTrue(app.buttons["Alice"].waitForExistence(timeout: 5))
        app.buttons["Alice"].tap()
        XCTAssertTrue(app.buttons["Alice, remove"].exists)
        XCTAssertFalse(app.buttons["Alice"].exists) // a chosen label is no longer suggested
        XCTAssertTrue(app.staticTexts["Waiting for payment"].exists)
        app.staticTexts["Bob, bike repair"].tap()
        XCTAssertTrue(app.buttons["Copy"].waitForExistence(timeout: 5)) // the payment request for that address
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'Bob, bike repair'")).firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'SegWit'")).firstMatch.exists)
    }

    func testReceivedPaymentShowsOnTheRequest() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "receivePaid", "WASABI_DEMO": "1"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Payment received"].waitForExistence(timeout: 5))
    }

    func testDecoyOpensWithDemoWallet() {
        let app = XCUIApplication()
        app.launchArguments = ["-decoy", "YES"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
    }
}
