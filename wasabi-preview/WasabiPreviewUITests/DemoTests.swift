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
    func testWideHomeShowsBalanceAndTransactionsTogether() throws {
        let app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "home", "WASABI_DEMO": "1", "WASABI_DISCREET": "0"]
        app.launch()
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["TRANSACTIONS"].isHittable)
        XCTAssertTrue(app.staticTexts["Balance Daily"].isHittable)
        let shots = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("build/shots")
        try FileManager.default.createDirectory(at: shots, withIntermediateDirectories: true)
        try XCUIScreen.main.screenshot().pngRepresentation.write(to: shots.appendingPathComponent("wide-home.png"))
    }

    func testDecoyOpensWithDemoWallet() {
        let app = XCUIApplication()
        app.launchArguments = ["-decoy", "YES"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
    }
}
