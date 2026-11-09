import AppKit
import SwiftUI

/// A `MenuBarExtra(.window)` panel grows when a section (Keys, Workspaces) expands but does not shrink back when it
/// collapses: SwiftUI then centers the smaller content in the still-tall window, so the panel appears to float away
/// from the menu bar. This view sits behind the menu's content, so its height is the content's height; whenever that
/// changes it sizes the window to fit, keeping the window's top edge where it is, so the panel grows and shrinks
/// downward only. A resize from the system is pinned back to the same top edge.
///
/// It also reports when the panel opens and closes. The panel and its SwiftUI content stay alive between opens, so
/// `onAppear`/`onDisappear` do not follow each open; the window's key state does.
struct MenuBarAnchor: NSViewRepresentable {
    var onOpen: (Bool) -> Void = { _ in }

    func makeNSView(context: Context) -> AnchorView { let view = AnchorView(); view.onOpen = onOpen; return view }
    func updateNSView(_ view: AnchorView, context: Context) { view.onOpen = onOpen }

    final class AnchorView: NSView {
        var onOpen: (Bool) -> Void = { _ in }
        private var top: CGFloat?
        private var correcting = false

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            NotificationCenter.default.removeObserver(self)
            top = nil
            guard let window else { return }
            let center = NotificationCenter.default
            // The system places the panel under its menu bar item each time it opens; remember that top edge then.
            center.addObserver(self, selector: #selector(opened(_:)), name: NSWindow.didBecomeKeyNotification, object: window)
            center.addObserver(self, selector: #selector(resized(_:)), name: NSWindow.didResizeNotification, object: window)
            center.addObserver(self, selector: #selector(closed(_:)), name: NSWindow.didResignKeyNotification, object: window)
            DispatchQueue.main.async { [weak self] in self?.remember(); self?.fit() }
        }

        override func setFrameSize(_ newSize: NSSize) {
            super.setFrameSize(newSize)
            fit()   // in the same pass, so no frame shows the content centered in the old window size
        }

        private func remember() {
            guard let frame = window?.frame else { return }
            top = frame.maxY
        }

        @objc private func opened(_ notification: Notification) { remember(); fit(); onOpen(true) }

        @objc private func closed(_ notification: Notification) { onOpen(false) }

        @objc private func resized(_ notification: Notification) {
            guard !correcting else { return }
            fit()
        }

        /// Make the window exactly as tall as the content, with its top edge at the remembered spot.
        private func fit() {
            guard !correcting, let window, bounds.height > 1 else { return }
            let top = self.top ?? window.frame.maxY
            let content = window.contentRect(forFrameRect: window.frame)
            let wanted = window.frameRect(forContentRect: NSRect(x: content.minX, y: 0, width: content.width, height: bounds.height))
            let target = NSRect(x: window.frame.minX, y: top - wanted.height, width: window.frame.width, height: wanted.height)
            if abs(target.height - window.frame.height) < 0.5 && abs(target.maxY - window.frame.maxY) < 0.5 { return }
            correcting = true
            window.disableScreenUpdatesUntilFlush()   // show the moved and resized window as one frame
            window.setFrame(target, display: true, animate: false)
            correcting = false
        }
    }
}
