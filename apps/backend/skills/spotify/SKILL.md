Spotify playback control and music search for EchoSpeak.

## When to use

When the user asks to:
- "Play some lofi hip hop"
- "What's currently playing?"
- "Skip this song" / "Next track"
- "Pause the music"
- "Search for songs by Artist"
- "Queue up Song by Artist"
- "Go back" / "Previous song"

## Tool reference

### spotify_current_track
Shows what's currently playing — track name, artist, album, progress. Use this when the user asks "what's playing?" or "what song is this?"

### spotify_play
Resume playback or play a specific track/album/playlist by URI. If no URI given, resumes current playback.

### spotify_pause
Pause the current playback.

### spotify_next
Skip to the next track in the queue.

### spotify_prev
Go back to the previous track.

### spotify_search
Search Spotify for tracks, artists, albums, or playlists. Returns top results with URIs that can be passed to spotify_play or spotify_queue.

### spotify_queue
Add a track to the playback queue by URI. Use after spotify_search to queue specific tracks.

## Requirements

The user must set `ALLOW_SPOTIFY=true` and configure Spotify API credentials (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`). OAuth2 authorization is required on first use.

## Output style

Keep responses short and music-themed. Use 🎵 emoji. For search results, show track name, artist, and URI in a clean list.
