//
//  ProfileView.swift
//  TASK
//
//  Created by Monish Kumar on 12/04/25.
//
import SwiftUI

struct ProfileView: View {
    @AppStorage("userName") var userName: String = "John Doe"
    @AppStorage("userImage") var userImageData: Data = UIImage(systemName: "person.crop.circle")!.pngData()!
    @State private var sessions: [Session] = []

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                if let img = UIImage(data: userImageData) {
                    Image(uiImage: img)
                        .resizable()
                        .clipShape(Circle())
                        .frame(width: 120, height: 120)
                        .overlay(Circle().stroke(.white, lineWidth: 3))
                        .shadow(radius: 5)
                        .padding(.top)
                }

                Text(userName)
                    .font(.title2.bold())

                summaryCard

                Text("Recent Sessions")
                    .font(.headline)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top)

                ForEach(sessions) { session in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("📌 Mode: \(session.mode.rawValue)")
                        Text("⏱ Duration: \(Int(session.duration) / 60) mins")
                        Text("🏆 Points: \(session.points)")
                        Text("🗓 Started: \(session.startTime.formatted(date: .abbreviated, time: .shortened))")
                    }
                    .padding()
                    .background(.ultraThinMaterial)
                    .cornerRadius(12)
                }
            }
            .padding()
        }
        .background(LinearGradient(colors: [.blue.opacity(0.15), .mint.opacity(0.2)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing))
        .onAppear {
            sessions = FocusSessionManager().loadSessions()
        }
    }

    var summaryCard: some View {
        VStack(spacing: 8) {
            Text("Total Points: \(totalPoints())")
                .font(.headline)
            Text("Unique Badges")
            badgeView()
        }
        .padding()
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    func totalPoints() -> Int {
        sessions.reduce(0) { $0 + $1.points }
    }

    func badgeView() -> some View {
        let all = sessions.flatMap { $0.badges }
        let uniqueBadges = Array(Set(all)).sorted(by: { $0.symbol < $1.symbol })
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack {
                ForEach(uniqueBadges, id: \.id) { badge in
                    Text(badge.symbol)
                        .padding(6)
                        .background(.thinMaterial)
                        .clipShape(Capsule())
                }
            }
        }
    }
}

// MARK: - Styling pass (spacing tweaks)
