// The menu bar mark: three nodes and a hub (design "A mesh", the owner's pick on 2026-10-30). The mark stays the same;
// a small badge carries the state: none when healthy, orange or red when something needs a look, and a faded mark
// with an empty ring when the supervisor cannot be reached.
import AppKit
import ControlCore

enum MenuBarIcon {
    static let size = NSSize(width: 18, height: 18)
    private static let badge = CGRect(x: 11.2, y: 0.4, width: 6.4, height: 6.4)

    static func image(_ severity: Severity) -> NSImage {
        if severity <= .ok {
            // Healthy: a template image, so the system tints it and inverts it when the menu is open.
            let image = NSImage(size: size, flipped: false) { _ in draw(ink: .black, faded: false); return true }
            image.isTemplate = true
            return image
        }
        // A coloured badge cannot be a template; the ink follows the bar's appearance when drawn.
        let image = NSImage(size: size, flipped: false) { _ in
            let dark = NSAppearance.currentDrawing().bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            draw(ink: dark ? .white : .black, faded: severity == .offline)
            mark(severity, ink: dark ? .white : .black)
            return true
        }
        image.isTemplate = false
        return image
    }

    private static func draw(ink: NSColor, faded: Bool) {
        guard let c = NSGraphicsContext.current?.cgContext else { return }
        c.saveGState(); defer { c.restoreGState() }
        c.setAlpha(faded ? 0.38 : 1)
        c.beginTransparencyLayer(auxiliaryInfo: nil)  // fade the mark as one, so overlaps do not darken
        c.setFillColor(ink.cgColor); c.setStrokeColor(ink.cgColor)
        c.setLineCap(.round); c.setLineWidth(1.4)
        let nodes = [CGPoint(x: 9, y: 15.5), CGPoint(x: 3, y: 4.5), CGPoint(x: 15, y: 4.5)], hub = CGPoint(x: 9, y: 8.3)
        for i in 0..<3 {
            c.move(to: nodes[i]); c.addLine(to: nodes[(i + 1) % 3])
            c.move(to: nodes[i]); c.addLine(to: hub)
        }
        c.strokePath()
        for p in nodes { dot(c, p, 2.3) }
        dot(c, hub, 1.9)
        c.endTransparencyLayer()
    }

    private static func mark(_ severity: Severity, ink: NSColor) {
        guard let c = NSGraphicsContext.current?.cgContext else { return }
        c.saveGState(); defer { c.restoreGState() }
        c.setBlendMode(.clear); c.fillEllipse(in: badge.insetBy(dx: -1.3, dy: -1.3)); c.setBlendMode(.normal)
        switch severity {
        case .warning: c.setFillColor(NSColor.systemOrange.cgColor); c.fillEllipse(in: badge)
        case .error: c.setFillColor(NSColor.systemRed.cgColor); c.fillEllipse(in: badge)
        default: c.setStrokeColor(ink.cgColor); c.setLineWidth(1.3); c.strokeEllipse(in: badge.insetBy(dx: 0.7, dy: 0.7))
        }
    }

    private static func dot(_ c: CGContext, _ p: CGPoint, _ r: CGFloat) {
        c.fillEllipse(in: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r))
    }
}
