//
//  FocusSessionManager.swift
//  TASK
//
//  Created by Monish Kumar on 12/04/25.
//

import Combine
import SwiftUI

class FocusSessionManager: ObservableObject {
    @Published var isFocusing = false
    @Published var currentMode: FocusMode?
    @Published var elapsedTime: TimeInterval = 0
    @Published var points = 0
    @Published var badges: [Badge] = []
    @Published var sessionStart: Date?

    private var timer: Timer?
    private let badgeTypes = [
        "🌵", "🎄", "🌲", "🌳", "🌴",
        "🍂", "🍁", "🍄",
        "🐅", "🦅", "🐵", "🐝",
    ]

    func startSession(mode: FocusMode) {
        currentMode = mode
        sessionStart = Date()
        isFocusing = true
        elapsedTime = 0
        points = 0
        badges = []

        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            self.elapsedTime += 1
            if Int(self.elapsedTime) % 120 == 0 {
                self.points += 1
                let symbol = self.badgeTypes.randomElement()!
                self.badges.append(Badge(symbol: symbol))
            }
        }
    }

    func stopSession() -> Session? {
        timer?.invalidate()
        timer = nil
        isFocusing = false
        guard let start = sessionStart, let mode = currentMode else { return nil }
        let session = Session(mode: mode, startTime: start, duration: elapsedTime, points: points, badges: badges)
        saveSession(session)
        return session
    }

    func resumeSessionIfNeeded() {
        if let data = UserDefaults.standard.data(forKey: "ongoingSession"),
           let saved = try? JSONDecoder().decode(Session.self, from: data) {
            let now = Date()
            let pastTime = now.timeIntervalSince(saved.startTime)
            startSession(mode: saved.mode)
            elapsedTime = pastTime
            points = Int(pastTime) / 120
        }
    }

    private func saveSession(_ session: Session) {
        var saved = loadSessions()
        saved.insert(session, at: 0)
        if let data = try? JSONEncoder().encode(saved) {
            UserDefaults.standard.set(data, forKey: "pastSessions")
        }
        UserDefaults.standard.removeObject(forKey: "ongoingSession")
    }

    func persistOngoingSession() {
        guard isFocusing,
              let start = sessionStart,
              let mode = currentMode else { return }
        let ongoing = Session(mode: mode, startTime: start, duration: elapsedTime, points: points, badges: badges)
        if let data = try? JSONEncoder().encode(ongoing) {
            UserDefaults.standard.set(data, forKey: "ongoingSession")
        }
    }

    func loadSessions() -> [Session] {
        guard let data = UserDefaults.standard.data(forKey: "pastSessions"),
              let sessions = try? JSONDecoder().decode([Session].self, from: data) else { return [] }
        return sessions
    }
}

// hotfix: widen session timer tolerance
