// broker/framing.py on the Swift side: a 4-byte big-endian length, then that many bytes of UTF-8 JSON.
// One request and one response per connection. A length of 0 or over 1 MiB is refused before any body is read.
import Foundation

public let maxFrameBytes = 1024 * 1024

public enum FrameError: Error, Equatable {
    case invalidLength(UInt32)
    case closed
    case timedOut
    case io(Int32)
}

public enum Framing {
    public static func encode(_ body: Data) throws -> Data {
        guard !body.isEmpty, body.count <= maxFrameBytes else { throw FrameError.invalidLength(UInt32(clamping: body.count)) }
        var length = UInt32(body.count).bigEndian
        return Data(bytes: &length, count: 4) + body
    }

    public static func length(_ header: Data) throws -> Int {
        guard header.count == 4 else { throw FrameError.closed }
        let size = header.reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
        guard size > 0, size <= UInt32(maxFrameBytes) else { throw FrameError.invalidLength(size) }
        return Int(size)
    }

    /// Reads one frame from a blocking socket whose SO_RCVTIMEO is already set.
    public static func read(_ fd: Int32) throws -> Data {
        let size = try length(readExactly(fd, 4))
        return try readExactly(fd, size)
    }

    public static func write(_ fd: Int32, _ body: Data) throws {
        let frame = try encode(body)
        try frame.withUnsafeBytes { raw in
            var offset = 0
            while offset < frame.count {
                let sent = Darwin.write(fd, raw.baseAddress! + offset, frame.count - offset)
                if sent < 0 {
                    if errno == EINTR { continue }
                    throw errno == EAGAIN ? FrameError.timedOut : FrameError.io(errno)
                }
                offset += sent
            }
        }
    }

    static func readExactly(_ fd: Int32, _ count: Int) throws -> Data {
        var data = Data(count: count)
        var offset = 0
        try data.withUnsafeMutableBytes { raw in
            while offset < count {
                let got = Darwin.read(fd, raw.baseAddress! + offset, count - offset)
                if got == 0 { throw FrameError.closed }
                if got < 0 {
                    if errno == EINTR { continue }
                    throw errno == EAGAIN ? FrameError.timedOut : FrameError.io(errno)
                }
                offset += got
            }
        }
        return data
    }
}
