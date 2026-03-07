import SwiftUI

struct SniperTab: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @State private var showAddWatchBlock = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    CardView("Sniper Service", icon: "scope") {
                        ConnectionRow(
                            label: "API Health",
                            icon: "bolt.horizontal.circle.fill",
                            isConnected: coordinator.sniperServiceHealthy,
                            activeColor: .purple
                        )

                        InfoRow(
                            label: "Discord Monitor",
                            value: coordinator.sniperMonitorEnabled
                                ? (coordinator.sniperMonitorConnected ? "Connected" : "Enabled (disconnected)")
                                : "Disabled"
                        )

                        HStack {
                            Button {
                                coordinator.refreshSniperData()
                            } label: {
                                HStack {
                                    Image(systemName: "arrow.clockwise")
                                    Text(coordinator.sniperIsLoading ? "Refreshing..." : "Refresh")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(coordinator.sniperIsLoading)
                        }
                    }

                    CardView("Watch Blocks", icon: "person.3.sequence.fill") {
                        if coordinator.sniperWatchBlocks.isEmpty {
                            Text("No watch blocks configured. Add one to start monitoring Discord sources.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            VStack(spacing: 10) {
                                ForEach(coordinator.sniperWatchBlocks) { block in
                                    WatchBlockRow(block: block) {
                                        coordinator.deleteSniperWatchBlock(id: block.id)
                                    }
                                }
                            }
                        }

                        Button {
                            showAddWatchBlock = true
                        } label: {
                            HStack {
                                Image(systemName: "plus.circle.fill")
                                Text("Add Watch Block")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }

                    CardView("Queued Coordinates", icon: "tray.full.fill") {
                        InfoRow(label: "Queue Size", value: "\(coordinator.sniperQueueState.size) / \(coordinator.sniperQueueState.maxSize)")

                        HStack(spacing: 10) {
                            Button {
                                coordinator.dispatchNextSniperCoordinate()
                            } label: {
                                HStack {
                                    Image(systemName: "paperplane.fill")
                                    Text("Dispatch Newest")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.blue)
                            .disabled(coordinator.sniperQueueState.size == 0 || coordinator.sniperIsLoading)

                            Button {
                                coordinator.clearSniperQueue()
                            } label: {
                                HStack {
                                    Image(systemName: "trash")
                                    Text("Clear")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                            .tint(.red)
                            .disabled(coordinator.sniperQueueState.size == 0 || coordinator.sniperIsLoading)
                        }

                        if coordinator.sniperQueueState.items.isEmpty {
                            Text("Queue is empty")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            VStack(spacing: 8) {
                                ForEach(Array(coordinator.sniperQueueState.items.reversed().prefix(5))) { item in
                                    QueueItemRow(item: item)
                                }
                            }
                        }
                    }

                    if !coordinator.sniperLastActionMessage.isEmpty {
                        CardView("Sniper Activity", icon: "waveform.path.ecg") {
                            Text(coordinator.sniperLastActionMessage)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Sniper")
            .sheet(isPresented: $showAddWatchBlock) {
                AddWatchBlockSheet(
                    knownServerIDs: coordinator.sniperKnownServerIDs,
                    knownChannelIDs: coordinator.sniperKnownChannelIDs,
                    knownUserIDs: coordinator.sniperKnownUserIDs
                ) { block in
                    coordinator.addSniperWatchBlock(block)
                }
            }
            .onAppear {
                coordinator.refreshSniperData()
            }
        }
    }
}

private struct WatchBlockRow: View {
    let block: SniperWatchBlock
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Server \(block.serverId)")
                    .font(.subheadline)
                    .fontWeight(.semibold)
                Spacer()
                Text(block.enabled ? "Enabled" : "Disabled")
                    .font(.caption2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(block.enabled ? Color.green.opacity(0.15) : Color.gray.opacity(0.2))
                    .cornerRadius(8)

                Button(role: .destructive, action: onDelete) {
                    Image(systemName: "trash")
                }
            }

            Text("Channel: \(block.channelId)")
                .font(.caption)
                .foregroundColor(.secondary)

            Text("Users: \(block.userIds.joined(separator: ", "))")
                .font(.caption)
                .foregroundColor(.secondary)

            if let geofence = block.geofence {
                Text(String(format: "Geofence: %.5f, %.5f · %.2f km", geofence.latitude, geofence.longitude, geofence.radiusKm))
                    .font(.caption2)
                    .foregroundColor(.blue)
            }
        }
        .padding(10)
        .background(Color(.tertiarySystemGroupedBackground))
        .cornerRadius(10)
    }
}

private struct QueueItemRow: View {
    let item: SniperQueueItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(String(format: "%.6f, %.6f", item.latitude, item.longitude))
                    .font(.system(.caption, design: .monospaced))
                Spacer()
                Text(item.source)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            Text("Queued: \(item.queuedAt.formatted(date: .abbreviated, time: .shortened))")
                .font(.caption2)
                .foregroundColor(.secondary)

            if item.pokemonName != nil || item.level != nil || item.cp != nil || item.ivPct != nil {
                HStack(spacing: 8) {
                    if let name = item.pokemonName {
                        Text(name)
                    }
                    if let level = item.level {
                        Text("L\(level)")
                    }
                    if let cp = item.cp {
                        Text("CP \(cp)")
                    }
                    if let ivPct = item.ivPct {
                        Text(String(format: "IV %.1f%%", ivPct))
                    }
                }
                .font(.caption2)
                .foregroundColor(.secondary)
            }

            if let ivAtk = item.ivAtk, let ivDef = item.ivDef, let ivSta = item.ivSta {
                Text("IV split: \(ivAtk)/\(ivDef)/\(ivSta)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            if let blockID = item.matchedBlockId {
                Text("Block ID: \(blockID)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            if let serverID = item.matchedServerId {
                Text("Server ID: \(serverID)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            if let channelID = item.matchedChannelId {
                Text("Channel ID: \(channelID)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            if let userID = item.matchedUserId {
                Text("User ID: \(userID)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            if let messageID = item.sourceMessageId {
                Text("Message ID: \(messageID)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            if let despawnEpoch = item.despawnEpoch {
                let remaining = Int(despawnEpoch - Date().timeIntervalSince1970)
                Text(remaining > 0 ? "Despawns in \(remaining)s" : "Expired")
                    .font(.caption2)
                    .foregroundColor(remaining > 0 ? .orange : .red)
            }
        }
        .padding(8)
        .background(Color(.tertiarySystemGroupedBackground))
        .cornerRadius(8)
    }
}

private struct AddWatchBlockSheet: View {
    @Environment(\.dismiss) private var dismiss

    @State private var serverId = ""
    @State private var channelId = ""
    @State private var userIdsCSV = ""
    @State private var enabled = true

    @State private var useGeofence = false
    @State private var geofenceLat = ""
    @State private var geofenceLon = ""
    @State private var geofenceRadius = ""

    let knownServerIDs: [String]
    let knownChannelIDs: [String]
    let knownUserIDs: [String]
    let onSave: (SniperWatchBlock) -> Void

    private var parsedUserIds: [String] {
        userIdsCSV
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private var canSave: Bool {
        guard !serverId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !channelId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !parsedUserIds.isEmpty
        else { return false }

        if useGeofence {
            return Double(geofenceLat) != nil && Double(geofenceLon) != nil && (Double(geofenceRadius) ?? 0) > 0
        }

        return true
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Discord Source") {
                    TextField("Server ID", text: $serverId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                    TextField("Channel ID", text: $channelId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                    TextField("User IDs (comma-separated)", text: $userIdsCSV)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                    Toggle("Enabled", isOn: $enabled)
                }

                if !knownServerIDs.isEmpty || !knownChannelIDs.isEmpty || !knownUserIDs.isEmpty {
                    Section("Previously Used IDs") {
                        if !knownServerIDs.isEmpty {
                            IDSuggestionRow(
                                title: "Server IDs",
                                ids: knownServerIDs,
                                onSelect: { serverId = $0 }
                            )
                        }
                        if !knownChannelIDs.isEmpty {
                            IDSuggestionRow(
                                title: "Channel IDs",
                                ids: knownChannelIDs,
                                onSelect: { channelId = $0 }
                            )
                        }
                        if !knownUserIDs.isEmpty {
                            IDSuggestionRow(
                                title: "User IDs",
                                ids: knownUserIDs,
                                onSelect: addUserIDSuggestion
                            )
                        }
                    }
                }

                Section("Geofence (optional)") {
                    Toggle("Use geofence", isOn: $useGeofence)
                    if useGeofence {
                        TextField("Latitude", text: $geofenceLat)
                            .keyboardType(.numbersAndPunctuation)
                        TextField("Longitude", text: $geofenceLon)
                            .keyboardType(.numbersAndPunctuation)
                        TextField("Radius km", text: $geofenceRadius)
                            .keyboardType(.numbersAndPunctuation)
                    }
                }
            }
            .navigationTitle("New Watch Block")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        var geofence: SniperGeofence?
                        if useGeofence,
                           let lat = Double(geofenceLat),
                           let lon = Double(geofenceLon),
                           let radius = Double(geofenceRadius),
                           radius > 0 {
                            geofence = SniperGeofence(latitude: lat, longitude: lon, radiusKm: radius)
                        }

                        let block = SniperWatchBlock(
                            serverId: serverId.trimmingCharacters(in: .whitespacesAndNewlines),
                            channelId: channelId.trimmingCharacters(in: .whitespacesAndNewlines),
                            userIds: parsedUserIds,
                            geofence: geofence,
                            enabled: enabled
                        )
                        onSave(block)
                        dismiss()
                    }
                    .disabled(!canSave)
                }
            }
        }
    }

    private func addUserIDSuggestion(_ id: String) {
        var ids = parsedUserIds
        if !ids.contains(id) {
            ids.append(id)
        }
        userIdsCSV = ids.joined(separator: ", ")
    }
}

private struct IDSuggestionRow: View {
    let title: String
    let ids: [String]
    let onSelect: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(ids, id: \.self) { id in
                        Button {
                            onSelect(id)
                        } label: {
                            Text(id)
                                .font(.system(.caption2, design: .monospaced))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color(.tertiarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}
