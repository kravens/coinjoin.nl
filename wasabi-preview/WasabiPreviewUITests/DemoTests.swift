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

    func testDecoyOpensWithDemoWallet() {
        let app = XCUIApplication()
        app.launchArguments = ["-decoy", "YES"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 5))
    }
}
