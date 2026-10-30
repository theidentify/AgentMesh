// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AgentMeshControl",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "agentmesh-presence", targets: ["agentmesh-presence"]),
        .executable(name: "AgentMeshControl", targets: ["AgentMeshControl"]),
    ],
    targets: [
        .target(name: "PresenceCore", linkerSettings: [.linkedFramework("LocalAuthentication")]),
        .executableTarget(name: "agentmesh-presence", dependencies: ["PresenceCore"]),
        .target(name: "ControlCore"),
        .executableTarget(name: "AgentMeshControl", dependencies: ["ControlCore"],
                          linkerSettings: [.linkedFramework("ServiceManagement")]),
        .testTarget(name: "PresenceCoreTests", dependencies: ["PresenceCore"]),
        .testTarget(name: "ControlCoreTests", dependencies: ["ControlCore"]),
    ]
)
