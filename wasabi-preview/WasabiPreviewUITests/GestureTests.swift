import XCTest

// Edge gestures on the home, against demo data (no RPC). Screenshots land in build/shots next to the app's own.
final class GestureTests: XCTestCase {
    private var app: XCUIApplication!
    private static let shots = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("build/shots")

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchEnvironment = ["WASABI_SCREEN": "home", "WASABI_DEMO": "1"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Balance Daily"].waitForExistence(timeout: 10))
    }

    private func drag(from: CGVector, to: CGVector) {
        app.coordinate(withNormalizedOffset: from).press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: to), withVelocity: .default, thenHoldForDuration: 0.1)
    }

    private func shot(_ name: String) throws {
        try FileManager.default.createDirectory(at: Self.shots, withIntermediateDirectories: true)
        try app.screenshot().pngRepresentation.write(to: Self.shots.appendingPathComponent(name + ".png"))
    }

    private var menu: XCUIElement { app.buttons["Retry now"] }
    private var scanner: XCUIElement { app.navigationBars["Scan to pay"] }

    func testLeftEdgeSwipeOpensMenu() throws {
        drag(from: CGVector(dx: 0.003, dy: 0.5), to: CGVector(dx: 0.6, dy: 0.5))
        XCTAssertTrue(menu.waitForExistence(timeout: 3))
        XCTAssertFalse(scanner.exists)
        try shot("gesture-menu")
    }

    func testRightEdgeSwipeOpensScanner() throws {
        drag(from: CGVector(dx: 0.997, dy: 0.5), to: CGVector(dx: 0.4, dy: 0.5))
        XCTAssertTrue(scanner.waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["Paste"].exists)
        XCTAssertFalse(menu.exists)
        try shot("scanner")
    }

    func testVerticalScrollAndRowSwipeOpenNothing() throws {
        // Open the coin accordion and swipe a row from inside the list: the row's own trailing action must still appear.
        app.staticTexts["Balance Daily"].tap()
        let row = app.staticTexts["Bitcoin meetup refund"]
        XCTAssertTrue(row.waitForExistence(timeout: 3))
        let y = row.frame.midY / app.frame.height
        drag(from: CGVector(dx: 0.85, dy: y), to: CGVector(dx: 0.5, dy: y)) // far enough to reveal, short of a full swipe that would fire it
        XCTAssertTrue(app.buttons["Exclude"].waitForExistence(timeout: 3))
        try shot("gesture-row-swipe")
        XCTAssertFalse(scanner.exists)
        // Near each edge, straight up: the list scrolls, nothing else opens.
        drag(from: CGVector(dx: 0.02, dy: 0.8), to: CGVector(dx: 0.02, dy: 0.3))
        drag(from: CGVector(dx: 0.98, dy: 0.8), to: CGVector(dx: 0.98, dy: 0.3))
        XCTAssertFalse(menu.exists)
        XCTAssertFalse(scanner.exists)
        XCTAssertFalse(app.staticTexts["Balance Daily"].isHittable) // it did scroll
    }

    func testPastedPaymentLandsInSend() throws {
        // The simulator shares one pasteboard; a BIP 321 link with an amount should skip straight to the fee step.
        UIPasteboard.general.string = "bitcoin:?bc=bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs&bc=bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4&amount=0.0012&label=Luke-Jr&lightning=lnbc1bogus"
        app.buttons["Scan to pay"].tap()
        XCTAssertTrue(scanner.waitForExistence(timeout: 3))
        app.buttons["Paste"].tap()
        XCTAssertTrue(app.navigationBars["Fee"].waitForExistence(timeout: 5))
        try shot("scanner-to-send")
        app.navigationBars["Fee"].buttons.firstMatch.tap() // Back
        XCTAssertTrue(app.navigationBars["Amount"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.textFields["0"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.textFields["0"].value as? String, "0.00120000")
    }

    func testUnpayableLinkStaysInScanner() throws {
        UIPasteboard.general.string = "bitcoin:?lightning=lnbc420bogusinvoice"
        app.buttons["Scan to pay"].tap()
        XCTAssertTrue(scanner.waitForExistence(timeout: 3))
        app.buttons["Paste"].tap()
        XCTAssertTrue(app.staticTexts["Wasabi pays on-chain only; this request has no on-chain address, only a Lightning invoice"].waitForExistence(timeout: 3))
        XCTAssertTrue(scanner.exists)
        try shot("scanner-unpayable")
    }
}
