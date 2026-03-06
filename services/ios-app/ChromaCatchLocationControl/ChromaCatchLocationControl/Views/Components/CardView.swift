import SwiftUI

/// Reusable card wrapper with consistent styling across all tabs.
struct CardView<Content: View>: View {
    let title: String?
    let icon: String?
    let content: Content

    init(_ title: String? = nil, icon: String? = nil,
         @ViewBuilder content: () -> Content) {
        self.title = title
        self.icon = icon
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title = title {
                HStack(spacing: 8) {
                    if let icon = icon {
                        Image(systemName: icon)
                            .foregroundColor(.green)
                            .font(.headline)
                    }
                    Text(title)
                        .font(.headline)
                        .fontWeight(.bold)
                }
            }
            content
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(16)
    }
}

/// Status row with icon, label, connection indicator, and optional detail text.
struct ConnectionRow: View {
    let label: String
    let icon: String
    let isConnected: Bool
    var detail: String? = nil
    var activeColor: Color = .green

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundColor(isConnected ? activeColor : .gray)
                .frame(width: 24)
            Text(label)
            Spacer()
            if let detail = detail {
                Text(detail)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
            }
            Circle()
                .fill(isConnected ? activeColor : .red.opacity(0.6))
                .frame(width: 10, height: 10)
        }
    }
}

/// Simple key-value row for status displays.
struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .font(.system(.body, design: .monospaced))
        }
    }
}

/// Signal strength indicator (1-4 bars).
struct SignalStrength: View {
    let rssi: Int

    var bars: Int {
        if rssi > -50 { return 4 }
        if rssi > -65 { return 3 }
        if rssi > -80 { return 2 }
        return 1
    }

    var body: some View {
        HStack(spacing: 1) {
            ForEach(1...4, id: \.self) { bar in
                RoundedRectangle(cornerRadius: 1)
                    .fill(bar <= bars ? Color.green : Color.gray.opacity(0.3))
                    .frame(width: 4, height: CGFloat(bar * 4 + 2))
            }
        }
    }
}

/// Grid-style status badge for the Dashboard.
struct StatusBadge: View {
    let label: String
    let isConnected: Bool
    var activeColor: Color = .green

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(isConnected ? activeColor : .red.opacity(0.6))
                .frame(width: 10, height: 10)
            Text(label)
                .font(.system(.caption, design: .monospaced))
                .fontWeight(.semibold)
                .lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color(.tertiarySystemGroupedBackground))
        .cornerRadius(10)
    }
}
