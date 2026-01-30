package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/google/uuid"
)

var ttsMu sync.Mutex
var activeTTSCmd *exec.Cmd

const (
	defaultAPIBase       = "http://localhost:8000"
	version              = "0.2.0"
	colorBg              = "0"
	colorPanel           = "0"
	colorHeaderBg        = "236"
	colorInputBg         = "0"
	colorInputBorder     = "240"
	colorBorder          = "240"
	colorAccent          = "75"
	colorText            = "252"
	colorMuted           = "244"
	colorThinking        = "220"
	colorTool            = "110"
	colorError           = "203"
	colorHighlight       = "27"
	colorSpinner         = "214"
	colorCode            = "111"
	colorLink            = "117"
	colorBold            = "255"
	colorVizActive       = "75"
	colorVizInactive     = "240"
	colorComposerBg      = "0"
	colorComposerFieldBg = "0"
	colorTick            = "42"
)

func formatDshowDeviceChoices(raw string) []string {
	parsed := parseDshowAudioDevices(raw)
	choices := []string{}
	for _, d := range parsed {
		friendly := strings.TrimSpace(d.Friendly)
		if friendly == "" {
			continue
		}
		alt := strings.TrimSpace(d.Alt)
		if alt != "" {
			choices = append(choices, fmt.Sprintf("%s (alt: %s)", friendly, alt))
		} else {
			choices = append(choices, friendly)
		}
	}
	return choices
}

// Stream event payloads from /query/stream (NDJSON).
type streamEvent struct {
	Type        string      `json:"type"`
	ID          string      `json:"id"`
	Name        string      `json:"name"`
	Input       string      `json:"input"`
	Output      string      `json:"output"`
	Error       string      `json:"error"`
	Response    string      `json:"response"`
	SpokenText  string      `json:"spoken_text"`
	Success     bool        `json:"success"`
	MemoryCount int         `json:"memory_count"`
	RequestID   string      `json:"request_id"`
	DocSources  interface{} `json:"doc_sources"`
}

type queryRequest struct {
	Message       string `json:"message"`
	IncludeMemory bool   `json:"include_memory"`
	ThreadID      string `json:"thread_id"`
}

type providerInfo struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Local    bool   `json:"local"`
	BaseURL  string `json:"base_url"`
}

type sessionsInfo struct {
	MultiAgentEnabled bool     `json:"multi_agent_enabled"`
	PoolMax           int      `json:"pool_max"`
	PoolSize          int      `json:"pool_size"`
	ThreadIDs         []string `json:"thread_ids"`
	LMStudioOnly      bool     `json:"lm_studio_only"`
	RuntimeProvider   *string  `json:"runtime_provider"`
}

type modelInfoMsg struct {
	provider string
	model    string
	isLocal  bool
}

type memoryInfoMsg struct {
	count int
}

type docInfoMsg struct {
	count int
}

type doctorInfoMsg struct {
	text string
	err  error
}

type sessionsInfoMsg struct {
	text string
	err  error
}

type lineType int

const (
	lineUser lineType = iota
	lineAssistant
	lineThinking
	lineTool
	lineError
)

const (
	viewViz = "visualizer"
)

const (
	vizIdle = iota
	vizTrigger
	vizTalk
)

type commandItem struct {
	Command     string
	Description string
}

type streamState struct {
	body   io.ReadCloser
	reader *bufio.Reader
}

type streamStartedMsg struct {
	state *streamState
}

type streamEventMsg struct {
	event streamEvent
}

type streamErrMsg struct {
	err error
}

type streamEndMsg struct{}

type streamKeepAliveMsg struct{}

type vizTickMsg struct{}

type ttsDoneMsg struct {
	id  int
	err error
}

type sttDoneMsg struct {
	text string
	err  error
}

type chatLine struct {
	kind    lineType
	content string
}

type model struct {
	apiBase       string
	threadID      string
	width         int
	height        int
	input         textinput.Model
	viewport      viewport.Model
	spinner       spinner.Model
	lines         []chatLine
	rendered      []string
	renderedW     int
	stream        *streamState
	sending       bool
	lastError     string
	lastRequest   string
	cwd           string
	commands      []commandItem
	commandIdx    int
	providerName  string
	modelName     string
	isLocal       bool
	modelLoaded   bool
	printModelInfo bool
	memoryCount   int
	docCount      int
	viewMode      string
	ttsID         int
	ttsQueue      []string
	toolRunNames  map[string]string
	usedTools     []string
	confirmActive bool
	confirmAction string
	vizActive     bool
	vizState      int
	vizTriggerT   int
	vizPhase      float64
	vizHeight     int
}

func initialModel(apiBase string) model {
	input := textinput.New()
	input.Placeholder = "Type a message..."
	input.Prompt = ""
	input.Focus()
	input.CharLimit = 0
	input.Cursor.SetMode(cursor.CursorBlink)
	input.Cursor.Style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.Cursor.TextStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.TextStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.PlaceholderStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted))
	input.PromptStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted))

	vp := viewport.New(0, 0)

	s := spinner.New()
	s.Spinner = spinner.Dot
	s.Style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorSpinner))

	cwd, err := os.Getwd()
	if err != nil {
		cwd = ""
	}

	m := model{
		apiBase:       apiBase,
		threadID:      uuid.NewString(),
		input:         input,
		viewport:      vp,
		spinner:       s,
		lines:         []chatLine{},
		cwd:           cwd,
		commands:      defaultCommands(),
		commandIdx:    0,
		providerName:  "Loading...",
		modelName:     "",
		isLocal:       false,
		modelLoaded:   false,
		printModelInfo: false,
		memoryCount:   0,
		docCount:      0,
		viewMode:      viewViz,
		toolRunNames:  map[string]string{},
		usedTools:     nil,
		confirmActive: false,
		confirmAction: "",
	}

	return m
}

func defaultCommands() []commandItem {
	return []commandItem{
		{Command: "/doctor", Description: "run doctor checks"},
		{Command: "/mic", Description: "record mic and transcribe"},
		{Command: "/session", Description: "manage sessions (thread_id)"},
		{Command: "/sessions", Description: "list active sessions"},
		{Command: "/status", Description: "show status"},
		{Command: "/model", Description: "show current model"},
		{Command: "/visualizer", Description: "show synth mouth"},
		{Command: "/help", Description: "show help"},
		{Command: "/commands", Description: "show all commands"},
		{Command: "/exit", Description: "exit the app"},
	}
}

func fetchSessionsCmd(apiBase string) tea.Cmd {
	return func() tea.Msg {
		url := strings.TrimRight(apiBase, "/") + "/sessions"
		req, err := http.NewRequest("GET", url, nil)
		if err != nil {
			return sessionsInfoMsg{text: "", err: err}
		}
		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return sessionsInfoMsg{text: "", err: err}
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return sessionsInfoMsg{text: "", err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))}
		}
		var info sessionsInfo
		if err := json.Unmarshal(body, &info); err != nil {
			return sessionsInfoMsg{text: "", err: err}
		}
		ids := info.ThreadIDs
		if len(ids) == 0 {
			ids = []string{"default"}
		}
		provider := "(default)"
		if info.RuntimeProvider != nil {
			val := strings.TrimSpace(*info.RuntimeProvider)
			if val != "" {
				provider = val
			}
		}
		lock := "false"
		if info.LMStudioOnly {
			lock = "true"
		}
		mode := "single-agent"
		if info.MultiAgentEnabled {
			mode = "multi-agent"
		}
		text := "Sessions\n" +
			"Mode: " + mode + "\n" +
			"Pool: " + fmt.Sprintf("%d/%d", info.PoolSize, info.PoolMax) + "\n" +
			"Runtime provider override: " + provider + "\n" +
			"LM Studio only: " + lock + "\n" +
			"Thread IDs: " + strings.Join(ids, ", ")
		return sessionsInfoMsg{text: text, err: nil}
	}
}

func warmupTTSCmd(apiBase string) tea.Cmd {
	return func() tea.Msg {
		payload, _ := json.Marshal(map[string]string{"text": "warmup"})
		req, err := http.NewRequest("POST", strings.TrimRight(apiBase, "/")+"/tts", bytes.NewReader(payload))
		if err != nil {
			return nil
		}
		req.Header.Set("Content-Type", "application/json")
		client := &http.Client{Timeout: 120 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return nil
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		return nil
	}
}

func recordAndTranscribeCmd(apiBase string, dur time.Duration) tea.Cmd {
	return func() tea.Msg {
		enabled, err := isSTTEnabled(apiBase)
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		if !enabled {
			return sttDoneMsg{text: "", err: fmt.Errorf("local STT is disabled (enable LOCAL_STT_ENABLED=true) or start the backend with STT enabled")}
		}

		wavOrWebm, err := recordMicToWebm(dur)
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		defer os.Remove(wavOrWebm)

		f, err := os.Open(wavOrWebm)
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		defer f.Close()
		data, err := io.ReadAll(f)
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}

		var body bytes.Buffer
		boundary := uuid.NewString()
		w := bufio.NewWriter(&body)
		_, _ = w.WriteString("--" + boundary + "\r\n")
		_, _ = w.WriteString("Content-Disposition: form-data; name=\"audio\"; filename=\"mic.webm\"\r\n")
		_, _ = w.WriteString("Content-Type: audio/webm\r\n\r\n")
		_, _ = w.Write(data)
		_, _ = w.WriteString("\r\n--" + boundary + "--\r\n")
		_ = w.Flush()

		req, err := http.NewRequest("POST", strings.TrimRight(apiBase, "/")+"/stt", bytes.NewReader(body.Bytes()))
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		req.Header.Set("Content-Type", "multipart/form-data; boundary="+boundary)
		client := &http.Client{Timeout: 120 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		defer resp.Body.Close()
		respBody, _ := io.ReadAll(resp.Body)
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return sttDoneMsg{text: "", err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(respBody)))}
		}
		var sttResp struct {
			Text string `json:"text"`
		}
		if err := json.Unmarshal(respBody, &sttResp); err != nil {
			return sttDoneMsg{text: "", err: err}
		}
		return sttDoneMsg{text: sttResp.Text, err: nil}
	}
}

func isSTTEnabled(apiBase string) (bool, error) {
	req, err := http.NewRequest("GET", strings.TrimRight(apiBase, "/")+"/stt/info", nil)
	if err != nil {
		return false, err
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return false, fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))
	}
	var info struct {
		Enabled bool `json:"enabled"`
	}
	if err := json.Unmarshal(body, &info); err != nil {
		return false, err
	}
	return info.Enabled, nil
}

func micDurationFromEnv() time.Duration {
	val := strings.TrimSpace(os.Getenv("ECHOSPEAK_MIC_SECONDS"))
	if val == "" {
		return 5 * time.Second
	}
	f, err := strconv.ParseFloat(val, 64)
	if err != nil || f <= 0 {
		return 5 * time.Second
	}
	if f > 30 {
		f = 30
	}
	return time.Duration(f * float64(time.Second))
}

func listDshowAudioDevices(ffmpeg string) ([]string, string) {
	cmd := exec.Command(ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy")
	b, _ := cmd.CombinedOutput()
	out := string(b)
	lines := strings.Split(out, "\n")
	devices := []string{}
	inAudio := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.Contains(trimmed, "DirectShow audio devices") {
			inAudio = true
			continue
		}
		if strings.Contains(trimmed, "DirectShow video devices") {
			if inAudio {
				break
			}
			continue
		}
		if !inAudio {
			continue
		}
		if strings.Contains(trimmed, "Alternative name") {
			continue
		}
		start := strings.Index(trimmed, "\"")
		if start == -1 {
			continue
		}
		rest := trimmed[start+1:]
		end := strings.Index(rest, "\"")
		if end == -1 {
			continue
		}
		name := strings.TrimSpace(rest[:end])
		if name != "" {
			devices = append(devices, name)
		}
	}
	return devices, strings.TrimSpace(out)
}

type dshowAudioDevice struct {
	Friendly string
	Alt      string
}

func parseDshowAudioDevices(raw string) []dshowAudioDevice {
	lines := strings.Split(raw, "\n")
	devices := []dshowAudioDevice{}
	inAudio := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.Contains(strings.ToLower(trimmed), "directshow audio devices") {
			inAudio = true
			continue
		}
		if strings.Contains(strings.ToLower(trimmed), "directshow video devices") {
			inAudio = false
			continue
		}

		start := strings.Index(trimmed, "\"")
		if start == -1 {
			continue
		}
		rest := trimmed[start+1:]
		end := strings.Index(rest, "\"")
		if end == -1 {
			continue
		}
		quoted := strings.TrimSpace(rest[:end])
		if quoted == "" {
			continue
		}

		if strings.Contains(trimmed, "Alternative name") {
			if len(devices) > 0 {
				devices[len(devices)-1].Alt = quoted
			}
			continue
		}

		// Fallback: if not explicitly in audio section, but line contains (audio), assume it is
		if inAudio || strings.Contains(trimmed, "(audio)") {
			devices = append(devices, dshowAudioDevice{Friendly: quoted})
		}
	}
	return devices
}

func normalizeDshowName(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "" {
		return ""
	}
	s = strings.ReplaceAll(s, "microphone", "")
	s = strings.ReplaceAll(s, "mic", "")
	s = strings.ReplaceAll(s, "(", " ")
	s = strings.ReplaceAll(s, ")", " ")
	s = strings.ReplaceAll(s, "-", " ")
	s = strings.ReplaceAll(s, "_", " ")
	s = strings.ReplaceAll(s, "\t", " ")
	s = strings.Join(strings.Fields(s), "")
	return s
}

func resolveDshowDevice(ffmpeg, requested string) (string, []string, string) {
	deviceNames, raw := listDshowAudioDevices(ffmpeg)
	parsed := parseDshowAudioDevices(raw)
	requested = strings.TrimSpace(requested)
	isAuto := requested == "" || strings.EqualFold(requested, "default") || strings.EqualFold(requested, "auto") || strings.EqualFold(requested, "system")

	if isAuto {
		if len(parsed) > 0 {
			return parsed[0].Friendly, deviceNames, raw
		}
		return "", deviceNames, raw
	}
	if strings.HasPrefix(strings.ToLower(requested), "@device_") {
		for _, d := range parsed {
			if d.Alt != "" && strings.EqualFold(d.Alt, requested) {
				return d.Alt, deviceNames, raw
			}
		}
		return requested, deviceNames, raw
	}
	for _, d := range parsed {
		if strings.EqualFold(d.Friendly, requested) {
			return d.Friendly, deviceNames, raw
		}
		if d.Alt != "" && strings.EqualFold(d.Alt, requested) {
			return d.Alt, deviceNames, raw
		}
	}

	requestedNorm := normalizeDshowName(requested)
	for _, d := range parsed {
		if d.Friendly != "" && strings.Contains(normalizeDshowName(d.Friendly), requestedNorm) {
			return d.Friendly, deviceNames, raw
		}
		if d.Alt != "" && strings.Contains(normalizeDshowName(d.Alt), requestedNorm) {
			return d.Alt, deviceNames, raw
		}
	}

	requestedLower := strings.ToLower(requested)
	for _, d := range parsed {
		if d.Friendly != "" && strings.Contains(strings.ToLower(d.Friendly), requestedLower) {
			return d.Friendly, deviceNames, raw
		}
		if d.Alt != "" && strings.Contains(strings.ToLower(d.Alt), requestedLower) {
			return d.Alt, deviceNames, raw
		}
	}

	return "", deviceNames, raw
}

func tailText(s string, maxLen int) string {
	s = strings.TrimSpace(s)
	if maxLen <= 0 || len(s) <= maxLen {
		return s
	}
	return "..." + s[len(s)-maxLen:]
}

func recordMicToWebm(dur time.Duration) (string, error) {
	ffmpeg := strings.TrimSpace(os.Getenv("ECHOSPEAK_FFMPEG"))
	if ffmpeg == "" {
		ffmpeg = "ffmpeg"
	}
	device := strings.TrimSpace(os.Getenv("ECHOSPEAK_MIC_DSHOW_DEVICE"))
	if runtime.GOOS == "windows" {
		autoDevice := device == "" || strings.EqualFold(device, "default") || strings.EqualFold(device, "system") || strings.EqualFold(device, "auto")
		if autoDevice {
			if name, err := defaultWindowsMicName(); err == nil && strings.TrimSpace(name) != "" {
				device = strings.TrimSpace(name)
			}
		}
		resolved, _, raw := resolveDshowDevice(ffmpeg, device)
		if resolved == "" {
			available := strings.Join(formatDshowDeviceChoices(raw), "; ")
			if available == "" {
				available = tailText(raw, 300)
			}
			if autoDevice {
				return "", fmt.Errorf("default mic not found. Set ECHOSPEAK_MIC_DSHOW_DEVICE to a dshow mic name or alt name (available: %s)", available)
			}
			if device == "" {
				return "", fmt.Errorf("set ECHOSPEAK_MIC_DSHOW_DEVICE to your dshow mic name (available: %s)", available)
			}
			return "", fmt.Errorf("mic device '%s' not found. Available: %s", device, available)
		}
		device = resolved
	}

	out, err := os.CreateTemp("", "echospeak-mic-*.webm")
	if err != nil {
		return "", err
	}
	path := out.Name()
	_ = out.Close()

	sec := fmt.Sprintf("%.2f", dur.Seconds())
	if runtime.GOOS == "windows" {
		inp := strings.TrimSpace(device)
		inp = strings.TrimSpace(strings.Trim(inp, "[]"))
		if len(inp) >= 2 {
			if (inp[0] == '"' && inp[len(inp)-1] == '"') || (inp[0] == '\'' && inp[len(inp)-1] == '\'') {
				inp = inp[1 : len(inp)-1]
			}
		}
		args := []string{"-hide_banner", "-loglevel", "error", "-y", "-f", "dshow", "-i", "audio=" + inp, "-t", sec, "-c:a", "libopus", "-b:a", "48k", path}
		cmd := exec.Command(ffmpeg, args...)
		b, err := cmd.CombinedOutput()
		if err != nil {
			_ = os.Remove(path)
			return "", fmt.Errorf("ffmpeg mic record failed: %v: %s", err, tailText(string(b), 800))
		}
		return path, nil
	}

	_ = os.Remove(path)
	return "", fmt.Errorf("mic recording not implemented for %s", runtime.GOOS)
}

func (m model) Init() tea.Cmd {
	return tea.Batch(
		textinput.Blink,
		m.spinner.Tick,
		fetchModelInfoCmd(m.apiBase),
		fetchMemoryInfoCmd(m.apiBase),
		fetchDocInfoCmd(m.apiBase),
		warmupTTSCmd(m.apiBase),
		vizTickCmd(),
	)
}

func vizTickCmd() tea.Cmd {
	return tea.Tick(60*time.Millisecond, func(time.Time) tea.Msg {
		return vizTickMsg{}
	})
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		if m.sending {
			m.rebuildContent()
		}
		return m, cmd
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resize()
		m.rebuildContent()
		return m, nil
	case vizTickMsg:
		inc := 0.03
		if m.vizActive {
			inc = 0.09
		}
		m.vizPhase += inc
		m.vizHeight = 0
		cmds = append(cmds, vizTickCmd())
	case ttsDoneMsg:
		if msg.id == m.ttsID {
			if msg.err != nil {
				m.vizActive = false
				m.vizState = vizIdle
				m.ttsQueue = nil
				m.lastError = msg.err.Error()
				break
			}
			m.ttsQueue = nil
			m.vizActive = false
			m.vizState = vizIdle
		}
		if msg.err != nil {
			m.lastError = msg.err.Error()
		}
	case sttDoneMsg:
		if msg.err != nil {
			m.lastError = msg.err.Error()
			m.lines = append(m.lines, chatLine{kind: lineError, content: "Mic error: " + truncate(m.lastError, 520)})
			m.rebuildContent()
			break
		}
		transcript := strings.TrimSpace(msg.text)
		if transcript != "" {
			m.input.SetValue(transcript)
			m.input.CursorEnd()
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Mic: " + truncate(transcript, 120)})
			m.rebuildContent()
		}
	case tea.KeyMsg:
		if msg.Type == tea.KeyCtrlC {
			m.closeStream()
			return m, tea.Quit
		}
		if msg.Type == tea.KeyEsc && m.isCommandPaletteActive() {
			m.input.SetValue("")
			m.commandIdx = 0
			return m, nil
		}
		if m.confirmActive && !m.isCommandPaletteActive() && !m.sending {
			trimmed := strings.TrimSpace(m.input.Value())
			switch msg.String() {
			case "y", "Y":
				if trimmed == "" {
					m.confirmActive = false
					m.confirmAction = ""
					var cmd tea.Cmd
					m, cmd = m.submitMessage("confirm")
					cmds = append(cmds, cmd)
					return m, tea.Batch(cmds...)
				}
			case "n", "N":
				if trimmed == "" {
					m.confirmActive = false
					m.confirmAction = ""
					var cmd tea.Cmd
					m, cmd = m.submitMessage("cancel")
					cmds = append(cmds, cmd)
					return m, tea.Batch(cmds...)
				}
			}
		}
		if msg.String() == "alt+1" {
			// Visualizer is the only view now
			m.viewMode = viewViz
			return m, nil
		}
		if msg.String() == "alt+r" {
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Mic: recording..."})
			m.rebuildContent()
			cmds = append(cmds, recordAndTranscribeCmd(m.apiBase, micDurationFromEnv()))
			break
		}

		if m.isCommandPaletteActive() {
			filtered := m.filteredCommands()
			switch msg.Type {
			case tea.KeyUp:
				if m.commandIdx > 0 {
					m.commandIdx -= 1
				}
				return m, nil
			case tea.KeyDown:
				if len(filtered) > 0 && m.commandIdx < len(filtered)-1 {
					m.commandIdx += 1
				}
				return m, nil
			case tea.KeyTab:
				if len(filtered) > 0 {
					m.commandIdx = (m.commandIdx + 1) % len(filtered)
				}
				return m, nil
			case tea.KeyShiftTab:
				if len(filtered) > 0 {
					m.commandIdx = (m.commandIdx + len(filtered) - 1) % len(filtered)
				}
				return m, nil
			case tea.KeyEnter:
				if len(filtered) > 0 {
					selected := filtered[m.commandIdx]
					m.input.SetValue(selected.Command + " ")
					m.commandIdx = 0
					return m, nil
				}
			}
		}

		switch msg.Type {
		case tea.KeyEnter:
			text := strings.TrimSpace(m.input.Value())
			if text == "" || m.sending {
				break
			}
			if m.confirmActive {
				normalized := strings.ToLower(strings.TrimSpace(text))
				if isConfirmText(normalized) {
					text = "confirm"
					m.confirmActive = false
					m.confirmAction = ""
				} else if isCancelText(normalized) {
					text = "cancel"
					m.confirmActive = false
					m.confirmAction = ""
				}
			}

			var cmd tea.Cmd
			m, cmd = m.submitMessage(text)
			cmds = append(cmds, cmd)
		}

		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		cmds = append(cmds, cmd)
		// Viewport update for visualizer
		m.viewport, cmd = m.viewport.Update(msg)
		cmds = append(cmds, cmd)
		m.syncCommandIdx()
	case streamStartedMsg:
		m.stream = msg.state
		cmds = append(cmds, nextStreamCmd(m.stream))
	case streamKeepAliveMsg:
		if m.stream != nil {
			cmds = append(cmds, nextStreamCmd(m.stream))
		}
	case streamEventMsg:
		m.handleStreamEvent(msg.event)
		if msg.event.Type == "final" {
			response := strings.TrimSpace(msg.event.Response)
			if response != "" {
				speakText := strings.TrimSpace(msg.event.SpokenText)
				if speakText == "" {
					speakText = response
				}
				m.ttsQueue = nil
				m.ttsID += 1
				m.vizActive = true
				m.vizState = vizTrigger
				m.vizTriggerT = 10
				cmds = append(cmds, speakTTSCmd(m.apiBase, speakText, m.ttsID))
			}
		}
		if m.stream != nil {
			cmds = append(cmds, nextStreamCmd(m.stream))
		}
	case streamErrMsg:
		m.lastError = msg.err.Error()
		m.dropTrailingThinking()
		m.lines = append(m.lines, chatLine{kind: lineError, content: "Error: " + truncate(m.lastError, 160)})
		m.closeStream()
		m.rebuildContent()
	case streamEndMsg:
		m.dropTrailingThinking()
		m.closeStream()
	case modelInfoMsg:
		m.providerName = msg.provider
		m.modelName = msg.model
		m.isLocal = msg.isLocal
		m.modelLoaded = true
		if m.printModelInfo {
			m.printModelInfo = false
			label := strings.TrimSpace(msg.provider)
			model := strings.TrimSpace(msg.model)
			if label == "" {
				label = "Unknown"
			}
			if model == "" {
				model = "(default)"
			}
			localFlag := "false"
			if msg.isLocal {
				localFlag = "true"
			}
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Model\nProvider: " + label + "\nModel: " + model + "\nLocal: " + localFlag})
			m.rebuildContent()
		}
	case memoryInfoMsg:
		m.memoryCount = msg.count
	case docInfoMsg:
		m.docCount = msg.count
	case doctorInfoMsg:
		if msg.err != nil {
			m.lines = append(m.lines, chatLine{kind: lineError, content: "Doctor error: " + truncate(msg.err.Error(), 180)})
		} else {
			text := strings.TrimSpace(msg.text)
			if text == "" {
				text = "(doctor returned no text)"
			}
			m.lines = append(m.lines, chatLine{kind: lineTool, content: text})
		}
		m.rebuildContent()
	case sessionsInfoMsg:
		if msg.err != nil {
			m.lines = append(m.lines, chatLine{kind: lineError, content: "Sessions error: " + truncate(msg.err.Error(), 180)})
		} else {
			text := strings.TrimSpace(msg.text)
			if text == "" {
				text = "(no sessions)"
			}
			m.lines = append(m.lines, chatLine{kind: lineTool, content: text})
		}
		m.rebuildContent()
	}

	if len(cmds) == 0 {
		return m, nil
	}
	return m, tea.Batch(cmds...)
}

func (m model) View() string {
	if m.width == 0 || m.height == 0 {
		return "loading..."
	}
	if len(m.lines) == 0 {
		return splashView(m)
	}
	// Only visualizer view now
	return visualizerView(m)
}

func (m *model) resize() {
	headerHeight := headerHeightLines()
	footerHeight := 1
	inputHeight := 4

	bodyHeight := m.height - headerHeight - footerHeight - inputHeight - 2
	if bodyHeight < 6 {
		bodyHeight = 6
	}

	m.viewport.Width = max(20, m.width-16)
	m.viewport.Height = bodyHeight
	m.input.Width = max(10, m.width-12)

	m.rebuildContent()
}

func headerHeightLines() int {
	return 1
}

func appendUnique(items []string, v string) []string {
	v = strings.TrimSpace(v)
	if v == "" {
		return items
	}
	for _, it := range items {
		if it == v {
			return items
		}
	}
	return append(items, v)
}

func (m *model) dropTrailingThinking() {
	for len(m.lines) > 0 && m.lines[len(m.lines)-1].kind == lineThinking {
		m.lines = m.lines[:len(m.lines)-1]
	}
}

func (m *model) rebuildContent() {
	width := max(20, m.viewport.Width-2)
	if m.renderedW != width || len(m.lines) < len(m.rendered) {
		m.rendered = nil
		m.renderedW = width
	}
	for i := len(m.rendered); i < len(m.lines); i++ {
		m.rendered = append(m.rendered, renderLine(*m, m.lines[i], width))
	}
	if m.sending && len(m.lines) > 0 && m.lines[len(m.lines)-1].kind == lineThinking {
		m.rendered[len(m.rendered)-1] = renderLine(*m, m.lines[len(m.lines)-1], width)
	}
	if len(m.rendered) == 0 {
		m.viewport.SetContent("")
		m.viewport.GotoBottom()
		return
	}
	var b strings.Builder
	for _, line := range m.rendered {
		b.WriteString(line)
		b.WriteString("\n\n")
	}
	m.viewport.SetContent(strings.TrimRight(b.String(), "\n"))
	m.viewport.GotoBottom()
}

func (m *model) isCommandPaletteActive() bool {
	value := strings.TrimSpace(m.input.Value())
	if len(value) == 0 {
		return false
	}
	return value[0] == '/'
}

func (m *model) filteredCommands() []commandItem {
	value := strings.TrimSpace(m.input.Value())
	if value == "/" {
		return m.commands
	}
	filtered := []commandItem{}
	for _, cmd := range m.commands {
		if strings.HasPrefix(cmd.Command, value) {
			filtered = append(filtered, cmd)
		}
	}
	if len(filtered) == 0 {
		return m.commands
	}
	return filtered
}

func (m *model) syncCommandIdx() {
	if !m.isCommandPaletteActive() {
		m.commandIdx = 0
		return
	}
	filtered := m.filteredCommands()
	if len(filtered) == 0 {
		m.commandIdx = 0
		return
	}
	if m.commandIdx >= len(filtered) {
		m.commandIdx = len(filtered) - 1
	}
}

func (m model) submitMessage(text string) (model, tea.Cmd) {
	text = strings.TrimSpace(text)
	if text == "" || m.sending {
		return m, nil
	}

	fields := strings.Fields(text)
	cmd0 := ""
	if len(fields) > 0 {
		cmd0 = fields[0]
	}

	if cmd0 == "/exit" {
		m.closeStream()
		return m, tea.Quit
	}

	if cmd0 == "/visualizer" {
		m.viewMode = viewViz
		m.input.SetValue("")
		m.commandIdx = 0
		return m, nil
	}

	if cmd0 == "/mic" {
		m.input.SetValue("")
		m.commandIdx = 0
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Mic: recording..."})
		m.rebuildContent()
		return m, recordAndTranscribeCmd(m.apiBase, micDurationFromEnv())
	}

	if text == "/status" || text == "/doctor" {
		m.input.SetValue("")
		m.commandIdx = 0
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Running doctor..."})
		m.rebuildContent()
		return m, fetchDoctorCmd(m.apiBase, m.threadID)
	}

	if text == "/model" {
		m.input.SetValue("")
		m.commandIdx = 0
		m.printModelInfo = true
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Refreshing model info..."})
		m.rebuildContent()
		return m, fetchModelInfoCmd(m.apiBase)
	}

	if cmd0 == "/help" {
		m.input.SetValue("")
		m.commandIdx = 0
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Help\n\n"+
			"Sessions:\n"+
			"- /sessions              list active sessions (thread_id)\n"+
			"- /session               show current session\n"+
			"- /session new           create and switch to a new session\n"+
			"- /session use <id>      switch to an existing session id\n\n"+
			"Ops:\n"+
			"- /status or /doctor     run doctor checks (optionally scoped to current session)\n"+
			"- /model                 refresh current provider/model\n\n"+
			"Input:\n"+
			"- /mic                   record + transcribe (Alt+R)\n"+
			"- /exit                  quit"})
		m.rebuildContent()
		return m, nil
	}

	if cmd0 == "/commands" {
		m.input.SetValue("")
		m.commandIdx = 0
		var b strings.Builder
		b.WriteString("Commands\n")
		for _, c := range m.commands {
			b.WriteString("- ")
			b.WriteString(c.Command)
			if strings.TrimSpace(c.Description) != "" {
				b.WriteString("  ")
				b.WriteString(c.Description)
			}
			b.WriteString("\n")
		}
		m.lines = append(m.lines, chatLine{kind: lineTool, content: strings.TrimSpace(b.String())})
		m.rebuildContent()
		return m, nil
	}

	if cmd0 == "/sessions" {
		m.input.SetValue("")
		m.commandIdx = 0
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Fetching sessions..."})
		m.rebuildContent()
		return m, fetchSessionsCmd(m.apiBase)
	}

	if cmd0 == "/session" {
		sub := ""
		if len(fields) > 1 {
			sub = strings.ToLower(strings.TrimSpace(fields[1]))
		}

		switch sub {
		case "", "current":
			m.input.SetValue("")
			m.commandIdx = 0
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Current session (thread_id): " + m.threadID})
			m.rebuildContent()
			return m, nil
		case "new":
			m.input.SetValue("")
			m.commandIdx = 0
			m.threadID = uuid.NewString()
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Switched to new session (thread_id): " + m.threadID})
			m.rebuildContent()
			return m, nil
		case "use":
			if len(fields) < 3 {
				m.input.SetValue("")
				m.commandIdx = 0
				m.lines = append(m.lines, chatLine{kind: lineError, content: "Usage: /session use <thread_id>"})
				m.rebuildContent()
				return m, nil
			}
			next := strings.TrimSpace(fields[2])
			if next == "" {
				next = uuid.NewString()
			}
			m.threadID = next
			m.input.SetValue("")
			m.commandIdx = 0
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Switched session (thread_id): " + m.threadID})
			m.rebuildContent()
			return m, nil
		case "list":
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Fetching sessions..."})
			m.rebuildContent()
			return m, fetchSessionsCmd(m.apiBase)
		default:
			m.input.SetValue("")
			m.commandIdx = 0
			m.lines = append(m.lines, chatLine{kind: lineError, content: "Unknown subcommand. Try: /session (current), /session new, /session use <id>, /session list"})
			m.rebuildContent()
			return m, nil
		}
	}

	m.toolRunNames = map[string]string{}
	m.usedTools = nil
	m.lines = append(m.lines, chatLine{kind: lineUser, content: text})
	m.lines = append(m.lines, chatLine{kind: lineThinking, content: "Thinking: ..."})
	m.input.SetValue("")
	m.lastError = ""
	m.sending = true
	m.rebuildContent()
	return m, startStreamCmd(m.apiBase, m.threadID, text)
}

func isConfirmText(s string) bool {
	s = strings.ToLower(strings.TrimSpace(s))
	switch s {
	case "confirm", "yes", "y", "ok", "okay", "do it", "go ahead", "sure":
		return true
	default:
		return false
	}
}

func isCancelText(s string) bool {
	s = strings.ToLower(strings.TrimSpace(s))
	switch s {
	case "cancel", "no", "n", "stop", "never mind", "nevermind", "abort", "dismiss":
		return true
	default:
		return false
	}
}

func extractPendingAction(resp string) (string, bool) {
	low := strings.ToLower(resp)
	if !strings.Contains(low, "reply 'confirm'") && !strings.Contains(low, "reply \"confirm\"") {
		return "", false
	}
	promptIdx := strings.Index(low, "reply 'confirm'")
	if promptIdx == -1 {
		promptIdx = strings.Index(low, "reply \"confirm\"")
	}
	if promptIdx == -1 {
		return "", false
	}

	startPhrase := "i can do this:"
	startIdx := strings.LastIndex(low[:promptIdx], startPhrase)
	if startIdx == -1 {
		startPhrase = "i have a pending action:"
		startIdx = strings.LastIndex(low[:promptIdx], startPhrase)
	}

	var action string
	if startIdx != -1 {
		action = strings.TrimSpace(resp[startIdx+len(startPhrase) : promptIdx])
	} else {
		action = strings.TrimSpace(resp[:promptIdx])
	}

	action = strings.TrimSpace(strings.TrimRight(action, ".: "))
	return action, true
}

func (m *model) handleStreamEvent(evt streamEvent) {
	m.dropTrailingThinking()

	switch evt.Type {
	case "tool_start":
		name := evt.Name
		if name == "" {
			name = "tool"
		}
		m.toolRunNames[evt.ID] = name
		m.usedTools = appendUnique(m.usedTools, name)
		input := strings.TrimSpace(evt.Input)
		if input != "" {
			input = truncate(input, 160)
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Tool: " + name + " (" + input + ")"})
		} else {
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Tool: " + name})
		}
	case "tool_end":
		name := strings.TrimSpace(evt.Name)
		if name == "" {
			name = m.toolRunNames[evt.ID]
		}
		if name == "" {
			name = "tool"
		}
		output := strings.TrimSpace(evt.Output)
		if output == "" {
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Tool done: " + name})
		} else {
			snippet := truncate(output, 240)
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Tool result: " + name + " -> " + snippet})
		}
		delete(m.toolRunNames, evt.ID)
	case "tool_error":
		name := strings.TrimSpace(evt.Name)
		if name == "" {
			name = m.toolRunNames[evt.ID]
		}
		if name == "" {
			name = "tool"
		}
		errText := strings.TrimSpace(evt.Error)
		if errText == "" {
			errText = "tool error"
		}
		m.lines = append(m.lines, chatLine{kind: lineError, content: "Tool error: " + name + ": " + truncate(errText, 180)})
		delete(m.toolRunNames, evt.ID)
	case "memory_saved":
		m.memoryCount = evt.MemoryCount
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "💾 Memory saved (" + fmt.Sprintf("%d", m.memoryCount) + " items)"})
	case "final":
		m.lastRequest = evt.RequestID
		m.sending = false
		m.vizActive = true
		response := strings.TrimSpace(evt.Response)
		if response == "" {
			response = "(no response)"
		}
		if action, ok := extractPendingAction(response); ok {
			m.confirmActive = true
			m.confirmAction = action
		} else {
			m.confirmActive = false
			m.confirmAction = ""
		}
		formatted := formatResponse(response)
		m.lines = append(m.lines, chatLine{kind: lineAssistant, content: formatted})
		if len(m.usedTools) > 0 {
			m.lines = append(m.lines, chatLine{kind: lineTool, content: "Tools used: " + strings.Join(m.usedTools, ", ")})
		}
	case "error":
		m.lastRequest = evt.RequestID
		m.sending = false
		m.confirmActive = false
		m.confirmAction = ""
		errText := evt.Error
		if errText == "" {
			errText = "stream error"
		}
		m.lastError = errText
		m.lines = append(m.lines, chatLine{kind: lineError, content: "Error: " + truncate(errText, 160)})
	}

	m.rebuildContent()
}

func (m *model) closeStream() {
	if m.stream != nil && m.stream.body != nil {
		_ = m.stream.body.Close()
	}
	m.stream = nil
	m.sending = false
}

func startStreamCmd(apiBase, threadID, text string) tea.Cmd {
	return func() tea.Msg {
		payload := queryRequest{Message: text, IncludeMemory: true, ThreadID: threadID}
		buf, err := json.Marshal(payload)
		if err != nil {
			return streamErrMsg{err: err}
		}

		req, err := http.NewRequest("POST", strings.TrimRight(apiBase, "/")+"/query/stream", bytes.NewReader(buf))
		if err != nil {
			return streamErrMsg{err: err}
		}
		req.Header.Set("Content-Type", "application/json")

		client := &http.Client{}
		resp, err := client.Do(req)
		if err != nil {
			return streamErrMsg{err: err}
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			body, _ := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			return streamErrMsg{err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))}
		}

		return streamStartedMsg{state: &streamState{body: resp.Body, reader: bufio.NewReader(resp.Body)}}
	}
}

func nextStreamCmd(state *streamState) tea.Cmd {
	return func() tea.Msg {
		line, err := state.reader.ReadString('\n')
		if err != nil {
			if err == io.EOF {
				return streamEndMsg{}
			}
			return streamErrMsg{err: err}
		}
		line = strings.TrimSpace(line)
		if line == "" {
			return streamKeepAliveMsg{}
		}
		var evt streamEvent
		if err := json.Unmarshal([]byte(line), &evt); err != nil {
			return streamErrMsg{err: err}
		}
		return streamEventMsg{event: evt}
	}
}

func fetchModelInfoCmd(apiBase string) tea.Cmd {
	return func() tea.Msg {
		req, err := http.NewRequest("GET", strings.TrimRight(apiBase, "/")+"/provider", nil)
		if err != nil {
			return modelInfoMsg{provider: "Error", model: "", isLocal: false}
		}

		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return modelInfoMsg{provider: "Unknown", model: "", isLocal: false}
		}
		defer resp.Body.Close()

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return modelInfoMsg{provider: "Error", model: "", isLocal: false}
		}

		var info providerInfo
		if err := json.Unmarshal(body, &info); err != nil {
			return modelInfoMsg{provider: "Unknown", model: "", isLocal: false}
		}

		return modelInfoMsg{provider: info.Provider, model: info.Model, isLocal: info.Local}
	}
}

func fetchMemoryInfoCmd(apiBase string) tea.Cmd {
	return func() tea.Msg {
		req, err := http.NewRequest("GET", strings.TrimRight(apiBase, "/")+"/memory", nil)
		if err != nil {
			return memoryInfoMsg{count: 0}
		}

		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return memoryInfoMsg{count: 0}
		}
		defer resp.Body.Close()

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return memoryInfoMsg{count: 0}
		}

		var result map[string]interface{}
		if err := json.Unmarshal(body, &result); err != nil {
			return memoryInfoMsg{count: 0}
		}

		count, ok := result["count"].(float64)
		if !ok {
			return memoryInfoMsg{count: 0}
		}

		return memoryInfoMsg{count: int(count)}
	}
}

func fetchDocInfoCmd(apiBase string) tea.Cmd {
	return func() tea.Msg {
		req, err := http.NewRequest("GET", strings.TrimRight(apiBase, "/")+"/documents", nil)
		if err != nil {
			return docInfoMsg{count: 0}
		}

		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return docInfoMsg{count: 0}
		}
		defer resp.Body.Close()

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return docInfoMsg{count: 0}
		}

		var result map[string]interface{}
		if err := json.Unmarshal(body, &result); err != nil {
			return docInfoMsg{count: 0}
		}

		count, ok := result["count"].(float64)
		if !ok {
			return docInfoMsg{count: 0}
		}

		return docInfoMsg{count: int(count)}
	}
}

func fetchDoctorCmd(apiBase, threadID string) tea.Cmd {
	return func() tea.Msg {
		url := strings.TrimRight(apiBase, "/") + "/doctor"
		if strings.TrimSpace(threadID) != "" {
			url = url + "?thread_id=" + threadID
		}
		req, err := http.NewRequest("GET", url, nil)
		if err != nil {
			return doctorInfoMsg{text: "", err: err}
		}

		client := &http.Client{Timeout: 6 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return doctorInfoMsg{text: "", err: err}
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return doctorInfoMsg{text: "", err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))}
		}

		var parsed struct {
			Text string `json:"text"`
			OK   bool   `json:"ok"`
		}
		if err := json.Unmarshal(body, &parsed); err != nil {
			return doctorInfoMsg{text: "", err: err}
		}
		return doctorInfoMsg{text: parsed.Text, err: nil}
	}
}

func speakTTSCmd(apiBase, text string, id int) tea.Cmd {
	return func() tea.Msg {
		sanitized := sanitizeTTSText(text)
		if strings.TrimSpace(sanitized) == "" {
			return ttsDoneMsg{id: id, err: nil}
		}

		type ttsRequest struct {
			Text string `json:"text"`
		}

		payload, err := json.Marshal(ttsRequest{Text: sanitized})
		if err != nil {
			return ttsDoneMsg{id: id, err: err}
		}

		req, err := http.NewRequest("POST", strings.TrimRight(apiBase, "/")+"/tts", bytes.NewReader(payload))
		if err != nil {
			return ttsDoneMsg{id: id, err: err}
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-EchoSpeak-Client", "tui")

		client := &http.Client{Timeout: 120 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return ttsDoneMsg{id: id, err: err}
		}
		defer resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			body, _ := io.ReadAll(resp.Body)
			return ttsDoneMsg{id: id, err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))}
		}

		wavBytes, err := io.ReadAll(resp.Body)
		if err != nil {
			return ttsDoneMsg{id: id, err: err}
		}
		if err := playWavBytes(wavBytes); err != nil {
			return ttsDoneMsg{id: id, err: err}
		}
		return ttsDoneMsg{id: id, err: nil}
	}
}

func sanitizeTTSText(s string) string {
	if s == "" {
		return ""
	}
	var b strings.Builder
	b.Grow(len(s))
	lastSpace := false
	for _, r := range s {
		if r == '\uFE0F' || r == '\u200D' {
			continue
		}
		if unicode.Is(unicode.So, r) {
			continue
		}
		if r == '\n' || r == '\r' || r == '\t' || r == ' ' {
			if lastSpace {
				continue
			}
			b.WriteByte(' ')
			lastSpace = true
			continue
		}
		lastSpace = false
		b.WriteRune(r)
	}
	return strings.TrimSpace(b.String())
}

func playWavBytes(wav []byte) error {
	f, err := os.CreateTemp("", "echospeak-tts-*.wav")
	if err != nil {
		return err
	}
	name := f.Name()
	if _, err := f.Write(wav); err != nil {
		_ = f.Close()
		_ = os.Remove(name)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(name)
		return err
	}
	defer os.Remove(name)

	stopPrevious := func() {
		if activeTTSCmd == nil {
			return
		}
		if activeTTSCmd.Process != nil {
			_ = activeTTSCmd.Process.Kill()
		}
		activeTTSCmd = nil
	}

	switch runtime.GOOS {
	case "windows":
		escaped := strings.ReplaceAll(name, "'", "''")
		ps := "(New-Object Media.SoundPlayer '" + escaped + "').PlaySync()"
		cmd := exec.Command("powershell", "-NoProfile", "-Command", ps)
		ttsMu.Lock()
		stopPrevious()
		activeTTSCmd = cmd
		ttsMu.Unlock()
		err := cmd.Run()
		ttsMu.Lock()
		if activeTTSCmd == cmd {
			activeTTSCmd = nil
		}
		ttsMu.Unlock()
		return err
	case "darwin":
		cmd := exec.Command("afplay", name)
		ttsMu.Lock()
		stopPrevious()
		activeTTSCmd = cmd
		ttsMu.Unlock()
		err := cmd.Run()
		ttsMu.Lock()
		if activeTTSCmd == cmd {
			activeTTSCmd = nil
		}
		ttsMu.Unlock()
		return err
	default:
		cmd := exec.Command("aplay", name)
		ttsMu.Lock()
		stopPrevious()
		activeTTSCmd = cmd
		ttsMu.Unlock()
		err := cmd.Run()
		ttsMu.Lock()
		if activeTTSCmd == cmd {
			activeTTSCmd = nil
		}
		ttsMu.Unlock()
		return err
	}
}

func splashView(m model) string {
	// "OpenCode" style splash
	// 1. Logo (Blocky)
	// 2. Input Box mockup
	// 3. Shortcuts
	// 4. Tip
	// 5. Version (bottom right)

	// Small stylized Logo
	logoStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("252"))

	// Updated clean logo (Sleek font)
	logoTitle := `  _____     _           ____                  _    
 | ____|___| |__   ___ / ___| _ __   ___  __ _| | __
 |  _| / __| '_ \ / _ \___ \| '_ \ / _ \/ _  | |/ /
 | |__| (__| | | | (_) |___) | |_) |  __/ (_| |   < 
 |_____\___|_| |_|\___/|____/| .__/ \___|\__,_|_|\_\
                             |_|                    `

	logo := logoStyle.Render(logoTitle)

	// Real Input Card
	inputWidth := 60
	inputBoxStyle := lipgloss.NewStyle().
		Width(inputWidth).
		Background(lipgloss.Color("0")). // Pure black bg
		Padding(1, 4).
		MarginTop(1).    // Reduced from 3
		MarginBottom(1). // Reduced from 2
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("255")) // White border

	// Use REAL input view
	inputContent := m.input.View()

	// Bottom of input box: Build info
	buildInfo := lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Bold(true).Render("Build") + " " +
		lipgloss.NewStyle().Foreground(lipgloss.Color("255")).Bold(true).Render("EchoSpeak") + " " +
		lipgloss.NewStyle().Foreground(lipgloss.Color("244")).Render("v"+version)

	modelInfo := lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Render("Model ")
	if m.modelLoaded {
		modelInfo += lipgloss.NewStyle().Foreground(lipgloss.Color("255")).Render(m.providerName + " / " + m.modelName)
	} else {
		modelInfo += lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Render("Detecting...")
	}

	inputBox := inputBoxStyle.Render(inputContent + "\n\n" + buildInfo + "\n" + modelInfo)

	// Shortcuts
	shortcutStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("244"))
	keyStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("255")).Bold(true)

	sc := []string{
		keyStyle.Render("ctrl+t") + " variants",
		keyStyle.Render("tab") + " agents",
		keyStyle.Render("ctrl+p") + " commands",
		keyStyle.Render("alt+r") + " mic",
	}
	shortcuts := shortcutStyle.Render(strings.Join(sc, "   "))

	// Tip
	tipLabel := lipgloss.NewStyle().Foreground(lipgloss.Color("220")).Bold(true).Render("● Tip") // Gold/Yellow
	tipText := lipgloss.NewStyle().Foreground(lipgloss.Color("244")).Render("Press ") +
		lipgloss.NewStyle().Foreground(lipgloss.Color("255")).Render("alt+1") +
		lipgloss.NewStyle().Foreground(lipgloss.Color("244")).Render(" to enter visualizer mode directly")

	tip := lipgloss.NewStyle().MarginTop(1).Render(tipLabel + " " + tipText) // Reduced from 2

	header := renderHeaderBar(m)
	block := lipgloss.JoinVertical(lipgloss.Center,
		logo,
		inputBox,
		shortcuts,
		tip,
	)
	centered := lipgloss.Place(m.width, m.height-1, lipgloss.Center, lipgloss.Center, block)

	return header + "\n" + centered
}

func chatView(m model) string {
	// Render header FIRST
	header := renderHeaderBar(m)
	headerHeight := lipgloss.Height(header)
	if headerHeight < 2 {
		headerHeight = 2
	}

	// Calculate remaining height for content - leave LOTS of room for header
	inputHeight := 5
	availableHeight := m.height - headerHeight - inputHeight - 2 // Extra padding
	if availableHeight < 5 {
		availableHeight = 5
	}

	content := m.viewport.View()

	contentWidth := m.width - 6
	containerStyle := lipgloss.NewStyle().
		Width(contentWidth).
		Height(availableHeight).
		Background(lipgloss.Color(colorPanel)).
		Border(lipgloss.NormalBorder()).
		BorderForeground(lipgloss.Color(colorBorder)).
		Padding(1, 5)
	content = containerStyle.Render(content)

	centeredContent := lipgloss.Place(m.width, availableHeight, lipgloss.Center, lipgloss.Top, content)

	var paletteOverlay string
	if m.isCommandPaletteActive() {
		paletteWidth := min(80, max(50, m.width-8))
		paletteOverlay = renderCommandPalette(m, paletteWidth)
	}

	confirmOverlay := ""
	if m.confirmActive {
		confirmOverlay = renderConfirmPrompt(m)
	}

	inputBox := renderInputBox(m)

	mainView := lipgloss.JoinVertical(lipgloss.Left,
		header,
		centeredContent,
		inputBox,
	)

	// Handle overlays
	if paletteOverlay != "" {
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, paletteOverlay)
	}
	if confirmOverlay != "" {
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, confirmOverlay)
	}

	return mainView
}

func renderConfirmPrompt(m model) string {
	if !m.confirmActive {
		return ""
	}
	panelWidth := min(80, max(40, m.width-10))
	if panelWidth < 28 {
		panelWidth = max(22, m.width-4)
	}

	action := strings.TrimSpace(m.confirmAction)
	if action == "" {
		action = "This action requires confirmation."
	} else {
		action = "Action: " + action
	}

	title := lipgloss.NewStyle().
		Foreground(lipgloss.Color(colorAccent)).
		Bold(true).
		Render("Action requires confirmation")

	actionLine := lipgloss.NewStyle().Width(panelWidth - 4).Render(action)
	hint := lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Render("Press Y to confirm / N to cancel (or type confirm/cancel).")
	content := strings.Join([]string{title, actionLine, hint}, "\n")

	boxStyle := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Background(lipgloss.Color(colorPanel)).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color(colorAccent))

	box := boxStyle.Render(content)
	return lipgloss.NewStyle().Width(m.width).Align(lipgloss.Center).Render(box)
}

func visualizerView(m model) string {
	width := m.width
	height := m.height

	// Render header FIRST to get actual height
	header := renderHeaderBar(m)
	headerH := lipgloss.Height(header)
	if headerH < 2 {
		headerH = 2
	}

	// Input box height - generous
	inputH := 5

	// Calculate remaining space - leave LOTS of room for header
	availH := height - headerH - inputH - 2 // Extra buffer
	if availH < 10 {
		availH = 10
	}

	// Split: 2/3 visualizer, 1/3 terminal
	termH := availH / 3
	if termH < 5 {
		termH = 5
	}
	vizH := availH - termH
	if vizH < 5 {
		vizH = 5
	}

	// Visualizer section
	vizBoxStyle := lipgloss.NewStyle().
		Width(width-2).
		Height(vizH).
		Align(lipgloss.Center, lipgloss.Center).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("255")) // White border

	vizContent := renderBigDots(width-6, vizH-2, m.vizPhase)
	if !m.vizActive {
		vizContent = lipgloss.NewStyle().Foreground(lipgloss.Color(colorVizInactive)).Render(vizContent)
	} else {
		vizContent = lipgloss.NewStyle().Foreground(lipgloss.Color(colorVizActive)).Bold(true).Render(vizContent)
	}
	vizContent = lipgloss.Place(width-4, vizH-2, lipgloss.Center, lipgloss.Center, vizContent)
	vizSection := vizBoxStyle.Render(vizContent)

	// Terminal/Log section
	termBoxStyle := lipgloss.NewStyle().
		Width(width-2).
		Height(termH).
		Border(lipgloss.NormalBorder(), true, true, false, true).
		BorderForeground(lipgloss.Color("255")) // White border

	logContent := renderLogView(m, width-4, termH-2)
	termSection := termBoxStyle.Render(logContent)

	// Input
	inputBox := renderInputBox(m)

	// Main layout - header at TOP
	mainLayout := lipgloss.JoinVertical(lipgloss.Left,
		header,
		vizSection,
		termSection,
		inputBox,
	)

	// Handle overlays
	var paletteOverlay string
	if m.isCommandPaletteActive() {
		paletteWidth := min(80, max(50, m.width-8))
		paletteOverlay = renderCommandPalette(m, paletteWidth)
	}
	if paletteOverlay != "" {
		return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, paletteOverlay)
	}

	if m.confirmActive {
		confirmOverlay := renderConfirmPrompt(m)
		return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, confirmOverlay)
	}

	return mainLayout
}

func renderLogView(m model, w, h int) string {
	// Extract last H lines from m.lines
	// We want to format them as "SYSTEM [TIME] > content"
	var logs []string

	start := 0
	if len(m.lines) > h {
		start = len(m.lines) - h
	}

	for i := start; i < len(m.lines); i++ {
		line := m.lines[i]
		prefix := " [LOG] "
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted))

		switch line.kind {
		case lineThinking:
			prefix = " [THK] "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorThinking))
		case lineTool:
			prefix = " [SYS] "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorTool))
		case lineError:
			prefix = " [ERR] "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorError))
		case lineUser:
			prefix = " [USR] "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent))
		case lineAssistant:
			prefix = " [AI ] "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
		}

		// Truncate content for single line log
		content := strings.ReplaceAll(line.content, "\n", " ")
		if len(content) > w-10 {
			content = content[:w-10] + "..."
		}

		logs = append(logs, style.Render(prefix+content))
	}

	// Pad with empty lines if needed to fill height
	for len(logs) < h {
		logs = append(logs, "")
	}

	return strings.Join(logs, "\n")
}

func renderHeaderBar(m model) string {
	// Nav Bar: Inverted colors for MAX visibility
	// Left: Logo
	logoStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("0")).   // Black text
		Background(lipgloss.Color("255")). // White background
		Bold(true).
		Padding(0, 1).
		Render(" EchoSpeak ")

	// Right: Version
	versionStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("255")). // White text
		Background(lipgloss.Color("0")).   // Black background
		Padding(0, 1).
		Render("v" + version)

	// Status
	statusText := "OFFLINE"
	statusColor := lipgloss.Color("240")
	if m.modelLoaded {
		statusText = "ONLINE"
		statusColor = lipgloss.Color(colorTick)
		if m.vizActive {
			statusText = "ACTIVE"
			statusColor = lipgloss.Color(colorVizActive)
		}
	}

	statusBlock := ""
	if m.width > 60 {
		statusBlock = lipgloss.NewStyle().
			Foreground(statusColor).
			Padding(0, 1).
			Render("● " + statusText)
	}

	rightBlock := lipgloss.JoinHorizontal(lipgloss.Center, statusBlock, versionStyle)

	// Combine with spacer
	contentWidth := lipgloss.Width(logoStyle) + lipgloss.Width(rightBlock)
	gap := m.width - contentWidth
	if gap < 0 {
		gap = 0
	}
	spacer := strings.Repeat(" ", gap)

	bar := lipgloss.JoinHorizontal(lipgloss.Center, logoStyle, spacer, rightBlock)

	return lipgloss.NewStyle().
		Width(m.width).
		Background(lipgloss.Color("235")). // Dark gray
		Render(bar)
}

func renderInputBox(m model) string {
	barBg := lipgloss.Color(colorComposerBg)
	fieldBg := lipgloss.Color(colorComposerFieldBg)
	boxWidth := max(20, m.width-2) // Visualizer width
	innerWidth := max(10, boxWidth-6)

	labelPrefix := lipgloss.NewStyle().
		Foreground(lipgloss.Color(colorAccent)).
		Background(barBg).
		Bold(true).
		Render("Message")

	providerLabel := strings.TrimSpace(m.providerName)
	modelLabel := "Loading..."
	if m.modelLoaded {
		if m.modelName != "" {
			modelLabel = m.modelName
		} else {
			modelLabel = "(no model)"
		}
	}

	providerText := ""
	if providerLabel != "" {
		providerText = lipgloss.NewStyle().
			Foreground(lipgloss.Color(colorMuted)).
			Background(barBg).
			Render(providerLabel + "/")
	}

	modelText := lipgloss.NewStyle().
		Foreground(lipgloss.Color(colorText)).
		Background(barBg).
		Bold(true).
		Render(modelLabel)

	inputTextColor := lipgloss.Color(colorMuted)
	if m.isCommandPaletteActive() {
		inputTextColor = lipgloss.Color(colorAccent)
	}
	labelLine := lipgloss.NewStyle().
		Background(barBg).
		Width(innerWidth).
		Render(lipgloss.JoinHorizontal(lipgloss.Top, labelPrefix, "  ", providerText, modelText))

	inputField := lipgloss.NewStyle().
		Foreground(inputTextColor).
		Background(fieldBg).
		Padding(0, 1).
		Width(innerWidth).
		Render(m.input.View())

	content := lipgloss.JoinVertical(lipgloss.Top, labelLine, inputField)

	borderColor := "255" // Default white
	if m.isCommandPaletteActive() {
		borderColor = colorAccent
	} else if m.sending {
		borderColor = colorThinking
	}

	border := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color(borderColor)).
		Background(lipgloss.Color("0")). // Black
		Padding(0, 1).
		Width(boxWidth)

	rendered := border.Render(content)
	return lipgloss.NewStyle().Width(m.width).Align(lipgloss.Center).Render(rendered)
}

func renderVisualizerWidget(m model, width int) string {
	activeColor := lipgloss.NewStyle().Foreground(lipgloss.Color(colorVizActive)).Bold(true)
	inactiveColor := lipgloss.NewStyle().Foreground(lipgloss.Color(colorVizInactive))

	innerW := min(60, max(18, width-2))
	innerH := 7
	content := renderBigDots(innerW, innerH, m.vizPhase)
	if m.vizActive {
		return activeColor.Render(content)
	}
	return inactiveColor.Render(content)
}

func renderBigDots(w, h int, phase float64) string {
	grid := make([][]rune, h)
	for y := 0; y < h; y++ {
		grid[y] = []rune(strings.Repeat(" ", w))
	}

	centerX := w / 2
	centerY := h / 2

	// Draw a "mouth" or "pulse"
	numBars := 12
	barWidth := 3
	spacing := 1

	for i := 0; i < numBars; i++ {
		// Calculate height for each bar based on a sine wave + phase
		offset := float64(i) * 0.5
		amplitude := 0.5 + 0.5*math.Sin(phase*2.0+offset)
		if i == 0 || i == numBars-1 {
			amplitude *= 0.3
		} else if i == 1 || i == numBars-2 {
			amplitude *= 0.6
		}

		barHeight := int(float64(h-2) * amplitude)
		if barHeight < 1 {
			barHeight = 1
		}

		xStart := centerX - (numBars*(barWidth+spacing))/2 + i*(barWidth+spacing)
		yStart := centerY - barHeight/2

		for bh := 0; bh < barHeight; bh++ {
			for bw := 0; bw < barWidth; bw++ {
				px := xStart + bw
				py := yStart + bh
				if px >= 0 && px < w && py >= 0 && py < h {
					grid[py][px] = '█'
				}
			}
		}
	}

	rows := make([]string, 0, h)
	for y := 0; y < h; y++ {
		rows = append(rows, string(grid[y]))
	}
	return strings.Join(rows, "\n")
}

func renderFooterBar(m model) string {
	// Minimal footer, no help text
	return ""
}

func splitTTSChunks(s string) []string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	firstMaxLen := 120
	maxLen := 220
	parts := strings.FieldsFunc(s, func(r rune) bool {
		return r == '\n' || r == '.' || r == '!' || r == '?' || r == ';'
	})

	var out []string
	cur := ""
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		curMax := maxLen
		if len(out) == 0 {
			curMax = firstMaxLen
		}
		candidate := p
		if cur != "" {
			candidate = cur + ". " + p
		}
		if len(candidate) <= curMax {
			cur = candidate
			continue
		}
		if cur != "" {
			out = append(out, strings.TrimSpace(cur))
			cur = ""
		}
		curMax = maxLen
		if len(out) == 0 {
			curMax = firstMaxLen
		}
		for len(p) > curMax {
			out = append(out, strings.TrimSpace(p[:curMax]))
			p = strings.TrimSpace(p[curMax:])
			curMax = maxLen
		}
		cur = p
	}
	if strings.TrimSpace(cur) != "" {
		out = append(out, strings.TrimSpace(cur))
	}
	return out
}

func formatResponse(text string) string {
	if text == "" {
		return ""
	}

	lines := strings.Split(text, "\n")
	var formatted []string

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			formatted = append(formatted, "")
			continue
		}

		line = formatMarkdownLine(line)
		formatted = append(formatted, line)
	}

	result := strings.Join(formatted, "\n")
	result = strings.TrimSpace(result)
	result = strings.ReplaceAll(result, "\n\n", "\n\n")

	return result
}

func formatMarkdownLine(line string) string {
	line = strings.TrimSpace(line)

	if strings.HasPrefix(line, "###") {
		content := strings.TrimSpace(strings.TrimPrefix(line, "###"))
		return "▸ " + lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Bold(true).Render(content)
	}

	if strings.HasPrefix(line, "##") {
		content := strings.TrimSpace(strings.TrimPrefix(line, "##"))
		return "▸▸ " + lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Bold(true).Render(content)
	}

	if strings.HasPrefix(line, "#") && !strings.HasPrefix(line, "##") {
		content := strings.TrimSpace(strings.TrimPrefix(line, "#"))
		return "▸▸▸ " + lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Bold(true).Render(content)
	}

	if strings.HasPrefix(line, "- ") {
		content := strings.TrimSpace(strings.TrimPrefix(line, "- "))
		return "  • " + content
	}

	if strings.HasPrefix(line, "* ") {
		content := strings.TrimSpace(strings.TrimPrefix(line, "* "))
		return "  • " + content
	}

	if strings.HasPrefix(line, "-") && len(line) > 1 {
		content := strings.TrimSpace(strings.TrimPrefix(line, "-"))
		return "  • " + content
	}

	if strings.HasPrefix(line, "*") && len(line) > 1 {
		content := strings.TrimSpace(strings.TrimPrefix(line, "*"))
		return "  • " + content
	}

	if strings.Contains(line, "**") {
		parts := strings.Split(line, "**")
		var b strings.Builder
		for i, p := range parts {
			if i%2 == 1 {
				b.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color(colorBold)).Bold(true).Render(p))
			} else {
				b.WriteString(p)
			}
		}
		line = b.String()
	}

	if strings.Contains(line, "*") && !strings.HasPrefix(line, "  • ") {
		parts := strings.Split(line, "*")
		if len(parts) >= 3 {
			var b strings.Builder
			for i, p := range parts {
				if i%2 == 1 {
					b.WriteString(lipgloss.NewStyle().Italic(true).Render(p))
				} else {
					b.WriteString(p)
				}
			}
			line = b.String()
		}
	}

	if strings.Contains(line, "`") {
		parts := strings.Split(line, "`")
		var b strings.Builder
		for i, p := range parts {
			if i%2 == 1 {
				b.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color(colorCode)).Background(lipgloss.Color("235")).Render(p))
			} else {
				b.WriteString(p)
			}
		}
		line = b.String()
	}

	return line
}

func renderLine(m model, line chatLine, width int) string {
	textStyle := lipgloss.NewStyle().Foreground(lipgloss.Color(colorText)).Background(lipgloss.Color(colorPanel))

	switch line.kind {
	case lineUser:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Background(lipgloss.Color(colorPanel))
	case lineAssistant:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText)).Background(lipgloss.Color(colorPanel))
	case lineThinking:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorThinking)).Background(lipgloss.Color(colorPanel))
		wrapStyle := lipgloss.NewStyle().Width(width - 3).Background(lipgloss.Color(colorPanel))
		content := wrapStyle.Render(m.spinner.View() + " " + line.content)
		return textStyle.Render(content)
	case lineTool:
		if strings.HasPrefix(line.content, "▸") {
			textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorSpinner)).Background(lipgloss.Color(colorPanel))
		} else if strings.HasPrefix(line.content, "✓") {
			textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorTool)).Background(lipgloss.Color(colorPanel))
		} else if strings.HasPrefix(line.content, "💾") {
			textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Background(lipgloss.Color(colorPanel))
		} else if strings.HasPrefix(line.content, "✗") {
			textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorError)).Background(lipgloss.Color(colorPanel))
		} else {
			textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Background(lipgloss.Color(colorPanel))
		}
	case lineError:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorError)).Background(lipgloss.Color(colorPanel))
	}

	wrapStyle := lipgloss.NewStyle().Width(width - 3).Background(lipgloss.Color(colorPanel))
	content := wrapStyle.Render(line.content)
	return textStyle.Render(content)
}

func padLine(left, right string, width int) string {
	space := width - len(left) - len(right) - 2
	if space < 1 {
		space = 1
	}
	return left + strings.Repeat(" ", space) + right
}

func padRight(text string, width int) string {
	if width <= 0 {
		return text
	}
	if len(text) >= width {
		return text[:width]
	}
	return text + strings.Repeat(" ", width-len(text))
}

func renderCommandPalette(m model, width int) string {
	items := m.filteredCommands()
	if len(items) == 0 {
		return ""
	}
	maxItems := min(8, len(items))
	displayItems := items[:maxItems]

	headerStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color(colorAccent)).
		Bold(true).
		Padding(0, 1).
		Border(lipgloss.NormalBorder(), false, false, true, false).
		BorderForeground(lipgloss.Color(colorBorder)).
		Width(width - 4)
	header := headerStyle.Render("COMMANDS")
	rows := []string{header, ""}

	for idx, item := range displayItems {
		selected := idx == m.commandIdx
		cmdStyle := lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Bold(true)
		descStyle := lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted))

		prefix := "  "
		if selected {
			prefix = lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Render("> ")
			cmdStyle = cmdStyle.Foreground(lipgloss.Color("231")).Background(lipgloss.Color(colorHighlight))
			descStyle = descStyle.Foreground(lipgloss.Color("252")).Background(lipgloss.Color(colorHighlight))
		}

		colWidth := 16
		descWidth := max(14, width-colWidth-8)
		cmd := padRight(item.Command, colWidth)
		desc := truncate(item.Description, descWidth)

		line := prefix + cmdStyle.Render(cmd) + " " + descStyle.Render(desc)
		if selected {
			line = lipgloss.NewStyle().Background(lipgloss.Color(colorHighlight)).Width(width - 4).Render(line)
		}
		rows = append(rows, line)
	}

	if len(items) > maxItems {
		rows = append(rows, "", lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Italic(true).Render(fmt.Sprintf("  ... and %d more", len(items)-maxItems)))
	}

	footer := lipgloss.NewStyle().
		Foreground(lipgloss.Color(colorMuted)).
		PaddingTop(1).
		Render("  ↑↓ navigate • Enter select • Esc close")
	rows = append(rows, footer)

	boxStyle := lipgloss.NewStyle().
		Width(width).
		Background(lipgloss.Color(colorPanel)).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color(colorAccent)).
		Padding(1, 0)

	return boxStyle.Render(strings.Join(rows, "\n"))
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func truncate(s string, maxLen int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.TrimSpace(s)
	if maxLen <= 0 {
		return ""
	}
	if len(s) <= maxLen {
		return s
	}
	if maxLen <= 3 {
		return s[:maxLen]
	}
	return s[:maxLen-3] + "..."
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func loadDotEnvFile(path string) {
	b, err := os.ReadFile(path)
	if err != nil {
		return
	}
	lines := strings.Split(string(b), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "#") {
			continue
		}
		idx := strings.Index(line, "=")
		if idx <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		val := strings.TrimSpace(line[idx+1:])
		if key == "" {
			continue
		}
		if len(val) >= 2 {
			if (val[0] == '"' && val[len(val)-1] == '"') || (val[0] == '\'' && val[len(val)-1] == '\'') {
				val = val[1 : len(val)-1]
			}
		}
		if os.Getenv(key) == "" {
			_ = os.Setenv(key, val)
		}
	}
}

func main() {
	exe, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exe)
		loadDotEnvFile(filepath.Join(exeDir, ".env"))
		loadDotEnvFile(filepath.Join(exeDir, "..", "backend", ".env"))
		loadDotEnvFile(filepath.Join(exeDir, "..", "..", "apps", "backend", ".env"))
	}
	wd, err := os.Getwd()
	if err == nil {
		loadDotEnvFile(filepath.Join(wd, ".env"))
		loadDotEnvFile(filepath.Join(wd, "..", "backend", ".env"))
		loadDotEnvFile(filepath.Join(wd, "apps", "backend", ".env"))
	}

	apiBase := strings.TrimSpace(os.Getenv("ECHOSPEAK_API_BASE"))
	if apiBase == "" {
		apiBase = defaultAPIBase
	}

	m := initialModel(apiBase)
	p := tea.NewProgram(m, tea.WithAltScreen())
	if err := p.Start(); err != nil {
		fmt.Println("error:", err)
		os.Exit(1)
	}
}
