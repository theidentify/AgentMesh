import AppKit
import SwiftUI

/// A `MenuBarExtra(.window)` panel resizes around its bottom edge, so expanding or collapsing a section (Keys,
/// Workspaces) moves its top away from the menu bar and it appears to float off. This pins the panel's top-left
/// corner to where it was when the panel opened, so it grows and shrinks downward only.
struct MenuBarAnchor: NSViewRepresentable {
    func makeNSView(context: Context) -> AnchorView { AnchorView() }
    func updateNSView(_ view: AnchorView, context: Context) {}

    final class AnchorView: NSView {
        private var topLeft: NSPoint?
        private var correcting = false

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            NotificationCenter.default.removeObserver(self)
            topLeft = nil
            guard let window else { return }
            let center = NotificationCenter.default
            // The system places the panel under its menu bar item each time it opens; remember that spot then.
            center.addObserver(self, selector: #selector(opened(_:)), name: NSWindow.didBecomeKeyNotification, object: window)
            center.addObserver(self, selector: #selector(resized(_:)), name: NSWindow.didResizeNotification, object: window)
            DispatchQueue.main.async { [weak self] in self?.remember() }
        }

        private func remember() {
            guard let frame = window?.frame else { return }
            topLeft = NSPoint(x: frame.minX, y: frame.maxY)
        }

        @objc private func opened(_ notification: Notification) { remember() }

        @objc private func resized(_ notification: Notification) {
            guard !correcting, let window, let topLeft else { return }
            let frame = window.frame
            if abs(frame.maxY - topLeft.y) < 0.5 && abs(frame.minX - topLeft.x) < 0.5 { return }
            correcting = true
            window.setFrameTopLeftPoint(topLeft)
            correcting = false
        }
    }
}
