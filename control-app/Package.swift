// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AgentMeshControl",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "agentmesh-presence", targets: ["agentmesh-presence"])],
    targets: [
        .target(name: "PresenceCore", linkerSettings: [.linkedFramework("LocalAuthentication")]),
        .executableTarget(name: "agentmesh-presence", dependencies: ["PresenceCore"]),
        .testTarget(name: "PresenceCoreTests", dependencies: ["PresenceCore"]),
    ]
)
