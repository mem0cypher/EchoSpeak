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
	"github.com/charmbracelet/x/ansi"
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
	width         int
	height        int
	apiBase       string
	threadID      string
	stream        *streamState
	sending       bool
	confirmActive bool
	confirmAction string
	stickToBottom bool
	viewport      viewport.Model
	spinner       spinner.Model
	lines         []chatLine
	rendered      []string
	renderedW     int
	input         textinput.Model
	lastError     string
	lastRequest   string
	cwd           string
	commands      []commandItem
	commandIdx    int
	commandOffset int
	providerName  string
	modelName     string
	activeWorkspace string
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
	vizActive     bool
	vizState      int
	vizTriggerT   int
	vizPhase      float64
	vizHeight     int
}

func initialModel(apiBase string) model {
	input := textinput.New()
	input.Placeholder = "Type a message…"
	input.Focus()
	input.CharLimit = 20000
	input.Cursor.SetMode(cursor.CursorBlink)
	input.Cursor.Style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.Cursor.TextStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.TextStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText))
	input.PlaceholderStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted))
	vp := viewport.New(0, 0)
	s := spinner.New()
	s.Spinner = spinner.Dot
	s.Style = lipgloss.NewStyle().Foreground(lipgloss.Color(colorSpinner))
	cwd, err := os.Getwd()
	if err != nil {
		cwd = ""
	}
	m := model{
		width:         0,
		height:        0,
		apiBase:       apiBase,
		threadID:      uuid.NewString(),
		input:         input,
		viewport:      vp,
		stickToBottom: true,
		spinner:       s,
		lines:         []chatLine{},
		lastError:     "",
		lastRequest:   "",
		cwd:           cwd,
		commands:      defaultCommands(),
		commandIdx:    0,
		commandOffset: 0,
		providerName:  "Loading…",
		modelName:     "",
		activeWorkspace: "",
		isLocal:       false,
		modelLoaded:   false,
		printModelInfo: false,
		memoryCount:   0,
		docCount:      0,
		viewMode:      viewViz,
		toolRunNames:  map[string]string{},
		usedTools:     nil,
		vizActive:     false,
		vizState:      vizIdle,
		vizTriggerT:   10,
		vizPhase:      0,
		vizHeight:     0,
	}
	return m
}

func defaultCommands() []commandItem {
	return []commandItem{
		{Command: "/doctor", Description: "run doctor checks"},
		{Command: "/session", Description: "manage sessions (thread_id)"},
		{Command: "/sessions", Description: "list active sessions"},
		{Command: "/onboard", Description: "show/select agent profile (coding/research/chat)"},
		{Command: "/workspaces", Description: "list available workspaces"},
		{Command: "/workspace", Description: "set workspace (e.g. /workspace coding)"},
		{Command: "/skills", Description: "list installed skills"},
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

func micNotSupportedCmd() tea.Cmd {
	return func() tea.Msg {
		return sttDoneMsg{text: "", err: fmt.Errorf("microphone input is not supported in the TUI right now; use the Web UI")}
	}
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
		if msg.Type == tea.KeyCtrlP {
			if m.confirmActive || m.sending {
				break
			}
			if m.isCommandPaletteActive() {
				// Close palette and restore previous input (if any).
				restore := strings.TrimSpace(m.lastRequest)
				m.input.SetValue(restore)
				m.commandIdx = 0
				m.commandOffset = 0
				return m, nil
			}
			// Open palette. Preserve any non-command draft input for restore.
			draft := m.input.Value()
			if strings.TrimSpace(draft) != "" && !strings.HasPrefix(strings.TrimSpace(draft), "/") {
				m.lastRequest = draft
			} else if strings.TrimSpace(draft) == "" {
				m.lastRequest = ""
			}
			m.input.SetValue("/")
			m.commandIdx = 0
			m.commandOffset = 0
			return m, nil
		}
		if msg.Type == tea.KeyEsc && m.isCommandPaletteActive() {
			m.input.SetValue("")
			m.commandIdx = 0
			m.commandOffset = 0
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
			m.lines = append(m.lines, chatLine{kind: lineError, content: "Mic: not supported in TUI (use Web UI)"})
			m.rebuildContent()
			break
		}

		if m.isCommandPaletteActive() {
			filtered := m.filteredCommands()
			switch msg.Type {
			case tea.KeyUp:
				if m.commandIdx > 0 {
					m.commandIdx -= 1
				}
				m.syncCommandIdx()
				return m, nil
			case tea.KeyDown:
				if len(filtered) > 0 && m.commandIdx < len(filtered)-1 {
					m.commandIdx += 1
				}
				m.syncCommandIdx()
				return m, nil
			case tea.KeyTab:
				if len(filtered) > 0 {
					m.commandIdx = (m.commandIdx + 1) % len(filtered)
				}
				m.syncCommandIdx()
				return m, nil
			case tea.KeyShiftTab:
				if len(filtered) > 0 {
					m.commandIdx = (m.commandIdx + len(filtered) - 1) % len(filtered)
				}
				m.syncCommandIdx()
				return m, nil
			case tea.KeyEnter:
				if len(filtered) > 0 {
					selected := filtered[m.commandIdx]
					// Insert into input and close palette so the user can type args.
					m.input.SetValue(selected.Command + " ")
					m.commandIdx = 0
					m.commandOffset = 0
					m.input.CursorEnd()
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
			// After sending a message, keep the viewport pinned.
			m.stickToBottom = true
		case tea.KeyPgUp:
			m.stickToBottom = false
			page := max(1, m.viewport.Height-1)
			m.viewport.LineUp(page)
			return m, nil
		case tea.KeyPgDown:
			page := max(1, m.viewport.Height-1)
			m.viewport.LineDown(page)
			return m, nil
		case tea.KeyCtrlU:
			m.stickToBottom = false
			m.viewport.HalfViewUp()
			return m, nil
		case tea.KeyCtrlD:
			m.viewport.HalfViewDown()
			return m, nil
		case tea.KeyHome:
			m.stickToBottom = false
			m.viewport.GotoTop()
			return m, nil
		case tea.KeyEnd:
			m.stickToBottom = true
			m.viewport.GotoBottom()
			return m, nil
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
	case tea.MouseMsg:
		if m.isCommandPaletteActive() {
			filtered := m.filteredCommands()
			if len(filtered) == 0 {
				break
			}
			maxItems := min(8, len(filtered))
			hasMore := len(filtered) > maxItems
			paletteWidth := min(80, max(50, m.width-8))
			paletteH := commandPaletteHeight(maxItems, hasMore)
			x0 := (m.width - paletteWidth) / 2
			y0 := (m.height - paletteH) / 2

			switch msg.Type {
			case tea.MouseWheelUp:
				if m.commandIdx > 0 {
					m.commandIdx--
					m.syncCommandIdx()
					return m, nil
				}
			case tea.MouseWheelDown:
				if m.commandIdx < len(filtered)-1 {
					m.commandIdx++
					m.syncCommandIdx()
					return m, nil
				}
			case tea.MouseLeft:
				// Approximate item hit-testing.
				// Items begin after border+padding and header+blank.
				// This won't be pixel-perfect, but will make clicking usable.
				itemStartY := y0 + 4
				itemEndY := itemStartY + maxItems - 1
				if msg.X >= x0 && msg.X <= x0+paletteWidth && msg.Y >= itemStartY && msg.Y <= itemEndY {
					idx := msg.Y - itemStartY
					absolute := m.commandOffset + idx
					if absolute >= 0 && absolute < len(filtered) {
						m.commandIdx = absolute
						m.syncCommandIdx()
						selected := filtered[m.commandIdx]
						m.input.SetValue(selected.Command + " ")
						m.commandIdx = 0
						m.commandOffset = 0
						m.input.CursorEnd()
						return m, nil
					}
				}
			}
		}
		// Mouse wheel scroll for chat output (viewport).
		switch msg.Type {
		case tea.MouseWheelUp:
			m.stickToBottom = false
			m.viewport.LineUp(3)
			return m, nil
		case tea.MouseWheelDown:
			m.viewport.LineDown(3)
			return m, nil
		}
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
	oldYOffset := m.viewport.YOffset
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
	if m.stickToBottom {
		m.viewport.GotoBottom()
	} else {
		m.viewport.YOffset = oldYOffset
	}
}

func (m *model) isCommandPaletteActive() bool {
	// Only trim leading whitespace; trailing whitespace is meaningful because
	// selecting a command inserts "/cmd " (with a trailing space) to close the palette.
	raw := m.input.Value()
	value := strings.TrimLeft(raw, " \t\r\n")
	if len(value) == 0 {
		return false
	}
	if value[0] != '/' {
		return false
	}
	// Palette should only be active while the user is typing the command token.
	// Once arguments start (space), allow normal Enter-to-submit.
	return !strings.Contains(value, " ")
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
		m.commandOffset = 0
		return
	}
	filtered := m.filteredCommands()
	if len(filtered) == 0 {
		m.commandIdx = 0
		m.commandOffset = 0
		return
	}
	if m.commandIdx >= len(filtered) {
		m.commandIdx = len(filtered) - 1
	}
	if m.commandIdx < 0 {
		m.commandIdx = 0
	}
	maxItems := min(8, len(filtered))
	if maxItems <= 0 {
		m.commandOffset = 0
		return
	}
	if m.commandOffset < 0 {
		m.commandOffset = 0
	}
	maxOffset := len(filtered) - maxItems
	if maxOffset < 0 {
		maxOffset = 0
	}
	if m.commandOffset > maxOffset {
		m.commandOffset = maxOffset
	}
	if m.commandIdx < m.commandOffset {
		m.commandOffset = m.commandIdx
	}
	if m.commandIdx >= m.commandOffset+maxItems {
		m.commandOffset = m.commandIdx - maxItems + 1
		if m.commandOffset > maxOffset {
			m.commandOffset = maxOffset
		}
	}
}

func commandPaletteHeight(visibleItems int, hasMore bool) int {
	// Rough geometry used for mouse hit-testing.
	// The palette renders:
	// - header + blank line
	// - visibleItems rows
	// - optional "... and N more" block (3 lines)
	// - footer (1 line)
	// - plus border + padding
	lines := 2 + visibleItems + 1
	if hasMore {
		lines += 3
	}
	// Border (2) + padding top/bottom (2)
	return lines + 4
}

func (m model) submitMessage(text string) (model, tea.Cmd) {
	text = strings.TrimSpace(text)
	if text == "" || m.sending {
		return m, nil
	}
	// Optimistically update workspace label on local slash commands.
	fields := strings.Fields(text)
	if len(fields) >= 2 {
		cmd := strings.ToLower(strings.TrimSpace(fields[0]))
		arg := strings.TrimSpace(fields[1])
		if cmd == "/onboard" || cmd == "/workspace" {
			m.activeWorkspace = arg
		}
	}

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

func extractWorkspaceFromResponse(resp string) string {
	low := strings.ToLower(resp)
	prefix := "workspace set to '"
	startIdx := strings.Index(low, prefix)
	if startIdx == -1 {
		return ""
	}
	valueStart := startIdx + len(prefix)
	if valueStart < 0 || valueStart >= len(resp) {
		return ""
	}
	closeRel := strings.Index(low[valueStart:], "'")
	if closeRel == -1 {
		return ""
	}
	valueEnd := valueStart + closeRel
	if valueEnd < valueStart || valueEnd > len(resp) {
		return ""
	}
	return strings.TrimSpace(resp[valueStart:valueEnd])
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
		m.lines = append(m.lines, chatLine{kind: lineTool, content: "Memory saved (" + fmt.Sprintf("%d", m.memoryCount) + " items)"})
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
		if ws := extractWorkspaceFromResponse(response); ws != "" {
			m.activeWorkspace = ws
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
			if runtime.GOOS != "windows" && runtime.GOOS != "darwin" {
				if localErr := speakLocalText(sanitized); localErr == nil {
					return ttsDoneMsg{id: id, err: nil}
				}
			}
			return ttsDoneMsg{id: id, err: err}
		}
		defer resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			body, _ := io.ReadAll(resp.Body)
			if runtime.GOOS != "windows" && runtime.GOOS != "darwin" {
				if localErr := speakLocalText(sanitized); localErr == nil {
					return ttsDoneMsg{id: id, err: nil}
				}
			}
			return ttsDoneMsg{id: id, err: fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))}
		}

		wavBytes, err := io.ReadAll(resp.Body)
		if err != nil {
			return ttsDoneMsg{id: id, err: err}
		}
		if err := playWavBytes(wavBytes); err != nil {
			if runtime.GOOS != "windows" && runtime.GOOS != "darwin" {
				if localErr := speakLocalText(sanitized); localErr == nil {
					return ttsDoneMsg{id: id, err: nil}
				}
			}
			return ttsDoneMsg{id: id, err: err}
		}
		return ttsDoneMsg{id: id, err: nil}
	}
}

func speakLocalText(text string) error {
	text = sanitizeTTSText(text)
	if strings.TrimSpace(text) == "" {
		return nil
	}

	// Prefer speech-dispatcher on Linux if available.
	if path, err := exec.LookPath("spd-say"); err == nil {
		cmd := exec.Command(path, "-w", text)
		return cmd.Run()
	}
	if path, err := exec.LookPath("espeak-ng"); err == nil {
		cmd := exec.Command(path, text)
		return cmd.Run()
	}
	if path, err := exec.LookPath("espeak"); err == nil {
		cmd := exec.Command(path, text)
		return cmd.Run()
	}

	return fmt.Errorf("no local TTS command found (install spd-say or espeak)")
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
		player := ""
		if _, err := exec.LookPath("pw-play"); err == nil {
			player = "pw-play"
		} else if _, err := exec.LookPath("paplay"); err == nil {
			player = "paplay"
		} else {
			player = "aplay"
		}
		cmd := exec.Command(player, name)
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
		keyStyle.Render("ctrl+c") + " quit",
		keyStyle.Render("ctrl+p") + " commands",
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

	if m.isCommandPaletteActive() {
		paletteWidth := min(80, max(50, m.width-8))
		paletteOverlay := renderCommandPalette(m, paletteWidth)
		if paletteOverlay != "" {
			return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, paletteOverlay)
		}
	}

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

		content := strings.ReplaceAll(line.content, "\n", " ")
		content = strings.TrimSpace(content)
		content = ansi.Strip(content)
		for _, row := range wrapForLog(content, prefix, w) {
			logs = append(logs, style.Render(row))
		}
	}

	// Keep only the last h visual lines.
	if h < 1 {
		h = 1
	}
	if len(logs) > h {
		logs = logs[len(logs)-h:]
	}
	for len(logs) < h {
		logs = append([]string{""}, logs...)
	}
	return strings.Join(logs, "\n")
}

func wrapForLog(content, prefix string, width int) []string {
	if width <= 0 {
		return []string{prefix + content}
	}
	indent := strings.Repeat(" ", lipgloss.Width(prefix))
	avail := width - lipgloss.Width(prefix)
	if avail < 5 {
		avail = 5
	}
	wrapped := wrapTextWidth(content, avail)
	if len(wrapped) == 0 {
		return []string{prefix}
	}
	rows := make([]string, 0, len(wrapped))
	for i, wl := range wrapped {
		if i == 0 {
			rows = append(rows, prefix+wl)
		} else {
			rows = append(rows, indent+wl)
		}
	}
	return rows
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

	wsLeft := ""
	if m.width > 60 {
		ws := strings.TrimSpace(m.activeWorkspace)
		if ws != "" {
			wsLeft = lipgloss.NewStyle().
				Foreground(lipgloss.Color("255")).
				Background(lipgloss.Color(colorAccent)).
				Padding(0, 1).
				Render(ws)
		}
	}

	leftBlock := logoStyle
	if wsLeft != "" {
		leftBlock = lipgloss.JoinHorizontal(lipgloss.Center, logoStyle, wsLeft)
	}

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

	wsBlock := ""
	if m.width > 60 {
		ws := strings.TrimSpace(m.activeWorkspace)
		if ws != "" {
			wsBlock = lipgloss.NewStyle().
				Foreground(lipgloss.Color("255")).
				Background(lipgloss.Color(colorAccent)).
				Padding(0, 1).
				Render(ws)
		}
	}

	rightBlock := lipgloss.JoinHorizontal(lipgloss.Center, statusBlock, wsBlock, versionStyle)

	// Combine with spacer
	contentWidth := lipgloss.Width(leftBlock) + lipgloss.Width(rightBlock)
	gap := m.width - contentWidth
	if gap < 0 {
		gap = 0
	}
	spacer := strings.Repeat(" ", gap)

	bar := lipgloss.JoinHorizontal(lipgloss.Center, leftBlock, spacer, rightBlock)

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
	innerWidth := width - 3
	if innerWidth < 10 {
		innerWidth = 10
	}

	switch line.kind {
	case lineUser:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorAccent)).Background(lipgloss.Color(colorPanel))
	case lineAssistant:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorText)).Background(lipgloss.Color(colorPanel))
	case lineThinking:
		textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color(colorThinking)).Background(lipgloss.Color(colorPanel))
		content := wrapWithPrefix(m.spinner.View()+" "+line.content, "", innerWidth)
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

	prefix := ""
	if line.kind == lineUser {
		prefix = "You: "
	} else if line.kind == lineAssistant {
		prefix = "AI: "
	}
	content := wrapWithPrefix(line.content, prefix, innerWidth)
	return textStyle.Render(content)
}

func wrapWithPrefix(content, prefix string, width int) string {
	if width <= 0 {
		return prefix + content
	}
	content = strings.ReplaceAll(content, "\r\n", "\n")
	lines := strings.Split(content, "\n")
	indent := strings.Repeat(" ", lipgloss.Width(prefix))

	var out []string
	for i, ln := range lines {
		pfx := indent
		if i == 0 {
			pfx = prefix
		}
		available := width - lipgloss.Width(pfx)
		if available < 5 {
			available = 5
		}
		wrappedLines := wrapTextWidth(ln, available)
		for j, wl := range wrappedLines {
			if j == 0 {
				out = append(out, pfx+wl)
			} else {
				out = append(out, indent+wl)
			}
		}
	}
	return strings.Join(out, "\n")
}

func wrapTextWidth(s string, width int) []string {
	s = strings.TrimRight(s, " ")
	if width <= 0 || s == "" {
		return []string{s}
	}
	if lipgloss.Width(s) <= width {
		return []string{s}
	}

	words := strings.Fields(s)
	if len(words) == 0 {
		return []string{""}
	}

	var out []string
	cur := ""
	for _, w := range words {
		if cur == "" {
			if lipgloss.Width(w) <= width {
				cur = w
				continue
			}
			// Hard-break a single long word.
			parts := breakLongWord(w, width)
			out = append(out, parts[:len(parts)-1]...)
			cur = parts[len(parts)-1]
			continue
		}

		candidate := cur + " " + w
		if lipgloss.Width(candidate) <= width {
			cur = candidate
			continue
		}

		out = append(out, cur)
		if lipgloss.Width(w) <= width {
			cur = w
			continue
		}
		parts := breakLongWord(w, width)
		out = append(out, parts[:len(parts)-1]...)
		cur = parts[len(parts)-1]
	}
	if strings.TrimSpace(cur) != "" {
		out = append(out, cur)
	}
	return out
}

func breakLongWord(word string, width int) []string {
	if width <= 0 {
		return []string{word}
	}
	var out []string
	cur := ""
	for _, r := range []rune(word) {
		candidate := cur + string(r)
		if cur != "" && lipgloss.Width(candidate) > width {
			out = append(out, cur)
			cur = string(r)
			continue
		}
		cur = candidate
	}
	if cur != "" {
		out = append(out, cur)
	}
	if len(out) == 0 {
		return []string{word}
	}
	return out
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
	offset := m.commandOffset
	if offset < 0 {
		offset = 0
	}
	if offset > len(items) {
		offset = len(items)
	}
	end := offset + maxItems
	if end > len(items) {
		end = len(items)
	}
	displayItems := items[offset:end]

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
		absoluteIdx := offset + idx
		selected := absoluteIdx == m.commandIdx
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
		remaining := len(items) - end
		if remaining < 0 {
			remaining = 0
		}
		if remaining > 0 {
			rows = append(rows, "", lipgloss.NewStyle().Foreground(lipgloss.Color(colorMuted)).Italic(true).Render(fmt.Sprintf("  ... and %d more", remaining)))
		}
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
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion())
	if err := p.Start(); err != nil {
		fmt.Println("error:", err)
		os.Exit(1)
	}
}
