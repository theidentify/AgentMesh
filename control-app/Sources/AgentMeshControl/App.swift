// AgentMesh Control: a menu bar client of the supervisor's control.sock. It holds no authority: tier 2 moves are
// approved by the supervisor's own Touch ID / Mac password sheet, not by anything this app says.
import AppKit
import ControlCore
import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate {
    let banners = ForegroundBanners()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)  // no Dock icon, also when run unbundled from swift run
        if Bundle.main.bundleIdentifier != nil { UNUserNotificationCenter.current().delegate = banners }
    }
}

@main
struct AgentMeshControlApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    private let model = StatusModel.shared  // App is created once; @State would need SwiftUIMacros

    var body: some Scene {
        MenuBarExtra {
            MenuView(model: model)
                .onAppear { model.menuOpen = true }
                .onDisappear { model.menuOpen = false }
        } label: {
            Image(nsImage: MenuBarIcon.image(model.icon, phase: model.spinPhase))
                .task { model.start() }
        }
        .menuBarExtraStyle(.window)

        Window("AgentMesh Audit", id: "audit") {
            AuditView(model: model)
        }
        .defaultSize(width: 720, height: 420)
    }
}
