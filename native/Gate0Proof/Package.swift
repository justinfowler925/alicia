// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Gate0Proof",
    platforms: [
        .macOS(.v14),
        .iOS(.v17),
    ],
    products: [
        .library(name: "Gate0Core", targets: ["Gate0Core"]),
        .executable(name: "Gate0Mac", targets: ["Gate0Mac"]),
        .executable(name: "Gate0Smoke", targets: ["Gate0Smoke"]),
    ],
    targets: [
        .target(
            name: "Gate0Core",
            path: "Sources/Gate0Core"
        ),
        .executableTarget(
            name: "Gate0Mac",
            dependencies: ["Gate0Core"],
            path: "Sources/Gate0Mac",
            swiftSettings: [
                .unsafeFlags(["-parse-as-library"]),
            ],
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("AVFoundation"),
            ]
        ),
        .executableTarget(
            name: "Gate0Smoke",
            dependencies: ["Gate0Core"],
            path: "Sources/Gate0Smoke",
            swiftSettings: [
                .unsafeFlags(["-parse-as-library"]),
            ]
        ),
    ]
)
