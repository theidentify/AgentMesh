// Draws AgentMesh Control's app icon (the menu bar mesh on a dark green squircle) into an .iconset directory;
// bundle.sh turns it into AppIcon.icns with iconutil. Usage: swift make-icon.swift <out.iconset> [preview.png]
import AppKit

func draw(_ c: CGContext, size: CGFloat) {
    let s = size / 1024
    // macOS icon grid: the body is 824 pt inside a 1024 canvas, corner radius about 185.
    let body = CGRect(x: 100 * s, y: 100 * s, width: 824 * s, height: 824 * s)
    let shape = CGPath(roundedRect: body, cornerWidth: 185 * s, cornerHeight: 185 * s, transform: nil)
    c.saveGState()
    c.setShadow(offset: CGSize(width: 0, height: -10 * s), blur: 28 * s, color: NSColor.black.withAlphaComponent(0.35).cgColor)
    c.addPath(shape); c.setFillColor(NSColor.black.cgColor); c.fillPath()
    c.restoreGState()
    c.saveGState(); c.addPath(shape); c.clip()
    let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [
        NSColor(red: 0.13, green: 0.20, blue: 0.17, alpha: 1).cgColor,
        NSColor(red: 0.05, green: 0.08, blue: 0.07, alpha: 1).cgColor] as CFArray, locations: [0, 1])!
    c.drawLinearGradient(gradient, start: CGPoint(x: body.midX, y: body.maxY), end: CGPoint(x: body.midX, y: body.minY), options: [])
    // The mark: the 18 pt menu bar mesh (it spans about x 0.7-17.6, y 2.2-17.8), centred in the body.
    let unit = 824 * s / 18 * 0.62, origin = CGPoint(x: body.midX - 9.15 * unit, y: body.midY - 9.9 * unit)
    func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: origin.x + x * unit, y: origin.y + y * unit) }
    let nodes = [p(9, 15.5), p(3, 4.5), p(15, 4.5)], hub = p(9, 8.3)
    let badge = CGRect(x: p(11.2, 0.4).x, y: p(11.2, 0.4).y, width: 6.4 * unit, height: 6.4 * unit)
    c.beginTransparencyLayer(auxiliaryInfo: nil)
    c.setStrokeColor(NSColor.white.cgColor); c.setLineWidth(1.4 * unit); c.setLineCap(.round)
    for i in 0..<3 { c.move(to: nodes[i]); c.addLine(to: nodes[(i + 1) % 3]); c.move(to: nodes[i]); c.addLine(to: hub) }
    c.strokePath()
    c.setFillColor(NSColor.white.cgColor)
    for n in nodes { c.fillEllipse(in: CGRect(x: n.x - 2.3 * unit, y: n.y - 2.3 * unit, width: 4.6 * unit, height: 4.6 * unit)) }
    c.fillEllipse(in: CGRect(x: hub.x - 1.9 * unit, y: hub.y - 1.9 * unit, width: 3.8 * unit, height: 3.8 * unit))
    // Cut the badge's ring out of the mark only, so the background shows through as in the menu bar.
    c.setBlendMode(.clear); c.fillEllipse(in: badge.insetBy(dx: -1.3 * unit, dy: -1.3 * unit)); c.setBlendMode(.normal)
    c.endTransparencyLayer()
    c.setFillColor(NSColor.systemGreen.cgColor); c.fillEllipse(in: badge.insetBy(dx: 0.6 * unit, dy: 0.6 * unit))
    c.restoreGState()
}

func png(_ px: Int) -> Data {
    let c = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    draw(c, size: CGFloat(px))
    return NSBitmapImageRep(cgImage: c.makeImage()!).representation(using: .png, properties: [:])!
}

let args = CommandLine.arguments
let out = URL(fileURLWithPath: args[1])
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    try png(base).write(to: out.appendingPathComponent("icon_\(base)x\(base).png"))
    try png(base * 2).write(to: out.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
if args.count > 2 { try png(1024).write(to: URL(fileURLWithPath: args[2])) }
