// The menu bar mark (design "A green", the owner's pick on 2026-10-31): three nodes and a hub, with one corner badge for
// the most important state. Healthy shows a green dot; while a job runs the hub becomes a spinning ring (a still ring
// under Reduce Motion). Offline fades the mark and shows an empty ring.
import AppKit
import ControlCore

enum MenuBarIcon {
    static let size = NSSize(width: 18, height: 18)
    private static let nodes = [CGPoint(x: 9, y: 15.5), CGPoint(x: 3, y: 4.5), CGPoint(x: 15, y: 4.5)]
    private static let hub = CGPoint(x: 9, y: 8.3)
    private static let badge = CGRect(x: 11.2, y: 0.4, width: 6.4, height: 6.4)

    /// phase in 0..<1 turns the running ring; nil draws it still.
    static func image(_ state: IconState, phase: CGFloat?) -> NSImage {
        let image = NSImage(size: size, flipped: false) { _ in
            guard let c = NSGraphicsContext.current?.cgContext else { return false }
            let dark = NSAppearance.currentDrawing().bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            draw(c, state, phase: phase, ink: (dark ? NSColor.white : NSColor.black).cgColor)
            return true
        }
        image.isTemplate = false
        image.accessibilityDescription = "AgentMesh: \(state.rawValue)"
        return image
    }

    private static func draw(_ c: CGContext, _ state: IconState, phase: CGFloat?, ink: CGColor) {
        c.saveGState()
        c.setAlpha(state == .offline ? 0.38 : 1)
        c.beginTransparencyLayer(auxiliaryInfo: nil)  // fade the mark as one, so overlaps do not darken
        c.setStrokeColor(ink); c.setFillColor(ink); c.setLineCap(.round); c.setLineWidth(1.4)
        for i in 0..<3 {
            c.move(to: nodes[i]); c.addLine(to: nodes[(i + 1) % 3])
            c.move(to: nodes[i]); c.addLine(to: hub)
        }
        c.strokePath()
        for p in nodes { dot(c, p, 2.3) }
        if state == .running {
            c.setBlendMode(.clear); dot(c, hub, 2.9); c.setBlendMode(.normal)
            c.setLineWidth(1.3)
            if let phase {
                let start = -phase * 2 * .pi
                c.addArc(center: hub, radius: 2.0, startAngle: start, endAngle: start + 1.5 * .pi, clockwise: false)
            } else {
                c.addEllipse(in: CGRect(x: hub.x - 2, y: hub.y - 2, width: 4, height: 4))
            }
            c.strokePath()
        } else {
            dot(c, hub, 1.9)
        }
        c.endTransparencyLayer()
        c.restoreGState()
        drawBadge(c, state, ink: ink)
    }

    private static func drawBadge(_ c: CGContext, _ state: IconState, ink: CGColor) {
        c.saveGState(); defer { c.restoreGState() }
        c.setBlendMode(.clear); c.fillEllipse(in: badge.insetBy(dx: -1.3, dy: -1.3)); c.setBlendMode(.normal)
        switch state {
        case .ok, .running:
            c.setFillColor(NSColor.systemGreen.cgColor); c.fillEllipse(in: badge.insetBy(dx: 0.6, dy: 0.6))
        case .warning:
            c.setFillColor(NSColor.systemOrange.cgColor); c.fillEllipse(in: badge)
        case .error:
            c.setFillColor(NSColor.systemRed.cgColor); c.fillEllipse(in: badge)
        case .needsYou:
            c.setFillColor(NSColor.systemBlue.cgColor); c.fillEllipse(in: badge)
            c.setFillColor(NSColor.white.cgColor); dot(c, CGPoint(x: badge.midX, y: badge.minY + 1.5), 0.7)
            c.setStrokeColor(NSColor.white.cgColor); c.setLineWidth(1.0); c.setLineCap(.round)
            c.move(to: CGPoint(x: badge.midX, y: badge.minY + 2.8)); c.addLine(to: CGPoint(x: badge.midX, y: badge.minY + 3.5))
            c.addArc(center: CGPoint(x: badge.midX, y: badge.minY + 4.4), radius: 0.95, startAngle: -.pi / 2, endAngle: .pi, clockwise: false)
            c.strokePath()
        case .maintenance:
            c.setFillColor(ink)
            c.fill(CGRect(x: badge.minX + 1.4, y: badge.minY + 0.9, width: 1.4, height: 4.6))
            c.fill(CGRect(x: badge.minX + 3.6, y: badge.minY + 0.9, width: 1.4, height: 4.6))
        case .moving:
            c.setStrokeColor(ink); c.setLineWidth(1.1); c.setLineCap(.round); c.setLineJoin(.round)
            let up = badge.minY + 4.3, down = badge.minY + 1.9, l = badge.minX + 0.6, r = badge.maxX - 0.6
            c.move(to: CGPoint(x: l, y: up)); c.addLine(to: CGPoint(x: r, y: up))
            c.move(to: CGPoint(x: r - 1.5, y: up + 1.3)); c.addLine(to: CGPoint(x: r, y: up)); c.addLine(to: CGPoint(x: r - 1.5, y: up - 1.3))
            c.move(to: CGPoint(x: r, y: down)); c.addLine(to: CGPoint(x: l, y: down))
            c.move(to: CGPoint(x: l + 1.5, y: down + 1.3)); c.addLine(to: CGPoint(x: l, y: down)); c.addLine(to: CGPoint(x: l + 1.5, y: down - 1.3))
            c.strokePath()
        case .offline:
            c.setStrokeColor(ink); c.setLineWidth(1.3); c.strokeEllipse(in: badge.insetBy(dx: 0.7, dy: 0.7))
        }
    }

    private static func dot(_ c: CGContext, _ p: CGPoint, _ r: CGFloat) {
        c.fillEllipse(in: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r))
    }
}
