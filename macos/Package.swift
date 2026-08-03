// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "DulusBarNative",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "DulusBarNative", targets: ["DulusBarNative"])
    ],
    targets: [
        .executableTarget(name: "DulusBarNative"),
        .testTarget(
            name: "DulusBarNativeTests",
            dependencies: ["DulusBarNative"]
        )
    ]
)
