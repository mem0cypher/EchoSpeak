import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {Box, Text, render, useApp} from 'ink';
import SelectInput from 'ink-select-input';
import TextInput from 'ink-text-input';
import open from 'open';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import http from 'node:http';

type ProviderId = 'gemini' | 'openai' | 'ollama' | 'lm_studio';

type Provider = {
	id: ProviderId;
	label: string;
	apiKeyEnv?: 'GEMINI_API_KEY' | 'OPENAI_API_KEY';
	modelEnv?: 'GEMINI_MODEL' | 'OPENAI_MODEL';
	models: string[];
	defaultModel: string;
};

const PROVIDERS: Provider[] = [
	{
		id: 'gemini',
		label: 'Google Gemini (Cloud, recommended)',
		apiKeyEnv: 'GEMINI_API_KEY',
		modelEnv: 'GEMINI_MODEL',
		models: ['gemini-2.5-pro', 'gemini-3.1-pro-preview', 'gemini-3-flash-preview'],
		defaultModel: 'gemini-2.5-pro'
	},
	{
		id: 'openai',
		label: 'OpenAI (Cloud)',
		apiKeyEnv: 'OPENAI_API_KEY',
		modelEnv: 'OPENAI_MODEL',
		models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1'],
		defaultModel: 'gpt-4o-mini'
	},
	{
		id: 'ollama',
		label: 'Ollama (Local)',
		models: [],
		defaultModel: 'llama3.2'
	},
	{
		id: 'lm_studio',
		label: 'LM Studio (Local)',
		models: [],
		defaultModel: 'local-model'
	}
];

type Step =
	| 'provider'
	| 'apiKey'
	| 'model'
	| 'profile'
	| 'confirm'
	| 'installing'
	| 'starting'
	| 'done'
	| 'error';

type SetupProfile = 'safe' | 'advanced';

type State = {
	provider?: Provider;
	apiKey?: string;
	model?: string;
	setupProfile: SetupProfile;
	error?: string;
};

function repoRootFromCwd() {
	return path.resolve(process.cwd(), '..', '..');
}

function backendDir() {
	return path.join(repoRootFromCwd(), 'apps', 'backend');
}

function webDir() {
	return path.join(repoRootFromCwd(), 'apps', 'web');
}

function backendSettingsPath() {
	return path.join(backendDir(), 'data', 'settings.json');
}

function backendSecretSettingsPath() {
	return path.join(backendDir(), 'data', 'settings.secrets.json');
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function mergeSettings(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
	const next: Record<string, unknown> = {...base};
	for (const [key, value] of Object.entries(patch)) {
		if (isRecord(value) && isRecord(next[key])) {
			next[key] = mergeSettings(next[key] as Record<string, unknown>, value);
		} else {
			next[key] = value;
		}
	}
	return next;
}

function readRuntimeSettings(): Record<string, unknown> {
	const settingsPath = backendSettingsPath();
	try {
		if (!fs.existsSync(settingsPath)) return {};
		const raw = fs.readFileSync(settingsPath, 'utf8').trim();
		if (!raw) return {};
		const parsed = JSON.parse(raw);
		return isRecord(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function readRuntimeSecretSettings(): Record<string, unknown> {
	const settingsPath = backendSecretSettingsPath();
	try {
		if (!fs.existsSync(settingsPath)) return {};
		const raw = fs.readFileSync(settingsPath, 'utf8').trim();
		if (!raw) return {};
		const parsed = JSON.parse(raw);
		return isRecord(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function writeJsonSettings(filePath: string, payload: Record<string, unknown>, chmodOwnerOnly = false) {
	fs.mkdirSync(path.dirname(filePath), {recursive: true});
	fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf8');
	if (chmodOwnerOnly && process.platform !== 'win32') {
		try {
			fs.chmodSync(filePath, 0o600);
		} catch {
		}
	}
}

function writeRuntimeSettings(provider: Provider, apiKey: string | undefined, model: string, setupProfile: SetupProfile) {
	const settingsPath = backendSettingsPath();
	const secretSettingsPath = backendSecretSettingsPath();

	const basePatch: Record<string, unknown> = {
		enable_system_actions: false,
		allow_file_write: false,
		allow_terminal_commands: false,
		allow_playwright: false,
		allow_desktop_automation: false,
		allow_open_application: false,
		allow_self_modification: false,
		file_tool_root: repoRootFromCwd(),
		terminal_command_allowlist: ['git', 'rg', 'ls', 'cat', 'python', 'pytest', 'npm', 'node', 'go', 'make'],
		memory_partition_enabled: true,
		trace_enabled: setupProfile === 'advanced',
	};

	const providerPatch: Record<string, unknown> = (() => {
		if (provider.id === 'openai') {
			return {
				use_local_models: false,
				default_cloud_provider: 'openai',
				openai: {
					model,
				},
			};
		}

		if (provider.id === 'gemini') {
			return {
				use_local_models: false,
				default_cloud_provider: 'gemini',
				gemini: {
					model,
				},
			};
		}

		if (provider.id === 'lm_studio') {
			return {
				use_local_models: true,
				local: {
					provider: 'lmstudio',
					model_name: model,
					base_url: 'http://localhost:1234',
				},
				embedding: {
					provider: 'lmstudio',
					model: 'text-embedding-nomic-embed-text-v1.5',
				},
			};
		}

		return {
			use_local_models: true,
			local: {
				provider: 'ollama',
				model_name: model,
				base_url: 'http://localhost:11434',
			},
		};
	})();

	const secretPatch: Record<string, unknown> = (() => {
		if (provider.id === 'openai' && apiKey) {
			return {openai: {api_key: apiKey}};
		}
		if (provider.id === 'gemini' && apiKey) {
			return {gemini: {api_key: apiKey}};
		}
		return {};
	})();

	const merged = mergeSettings(readRuntimeSettings(), mergeSettings(basePatch, providerPatch));
	const mergedSecrets = mergeSettings(readRuntimeSecretSettings(), secretPatch);
	writeJsonSettings(settingsPath, merged);
	if (Object.keys(mergedSecrets).length > 0) {
		writeJsonSettings(secretSettingsPath, mergedSecrets, true);
	}
}

function pythonInVenv() {
	const bin = process.platform === 'win32' ? 'Scripts' : 'bin';
	const exe = process.platform === 'win32' ? 'python.exe' : 'python';
	return path.join(backendDir(), '.venv', bin, exe);
}

function spawnDetached(cmd: string, args: string[], cwd: string) {
	const child = spawn(cmd, args, {
		cwd,
		detached: true,
		stdio: 'ignore'
	});
	child.unref();
	return child;
}

function probeHttp(url: URL, timeoutMs: number): Promise<boolean> {
	return new Promise(resolve => {
		const req = http.request(
			{
				host: url.hostname,
				port: Number(url.port),
				path: url.pathname,
				method: 'GET',
				timeout: timeoutMs
			},
			res => {
				res.resume();
				resolve(true);
			}
		);
		req.on('timeout', () => {
			req.destroy();
			resolve(false);
		});
		req.on('error', () => resolve(false));
		req.end();
	});
}

async function detectWebUiUrl(): Promise<string> {
	const portsToTry = [5173, 5174, 5175, 5176, 5177, 5178, 5179, 5180];
	for (const port of portsToTry) {
		const url = new URL(`http://localhost:${port}/`);
		const ok = await probeHttp(url, 250);
		if (ok) return url.toString().replace(/\/$/, '');
	}
	return 'http://localhost:5173';
}

async function waitForUrl(url: string, timeoutMs: number, intervalMs: number): Promise<boolean> {
	const started = Date.now();
	while (Date.now() - started < timeoutMs) {
		if (await probeHttp(new URL(url), Math.min(intervalMs, 500))) {
			return true;
		}
		await new Promise(resolve => setTimeout(resolve, intervalMs));
	}
	return false;
}

function App() {
	const {exit} = useApp();
	const [step, setStep] = useState<Step>('provider');
	const [state, setState] = useState<State>({setupProfile: 'safe'});
	const [webUiUrl, setWebUiUrl] = useState<string>('');
	const [apiKeyInput, setApiKeyInput] = useState('');
	const [localModelInput, setLocalModelInput] = useState('');

	const providerItems = useMemo(
		() =>
			PROVIDERS.map(p => ({
				label: p.label,
				value: p.id
			})),
		[]
	);

	const profileItems = useMemo(
		() => [
			{label: 'Safe — all system actions stay off until you enable them later', value: 'safe'},
			{label: 'Advanced — same safe tool defaults, plus tracing enabled', value: 'advanced'},
		],
		[]
	);

	const onSelectProvider = useCallback(
		(item: {label: string; value: string}) => {
			const provider = PROVIDERS.find(p => p.id === item.value) ?? PROVIDERS[0];
			setState(prev => ({...prev, provider}));
			setApiKeyInput('');
			setLocalModelInput('');
			if (provider.apiKeyEnv) {
				setStep('apiKey');
			} else {
				setStep('model');
			}
		},
		[]
	);

	const modelItems = useMemo(() => {
		const provider = state.provider;
		if (!provider) return [];
		if (!provider.models.length) return [];
		return provider.models.map(m => ({label: m, value: m}));
	}, [state.provider]);

	const onSelectModel = useCallback(
		(item: {label: string; value: string}) => {
			setState(prev => ({...prev, model: item.value}));
			setStep('profile');
		},
		[]
	);

	const onSelectProfile = useCallback(
		(item: {label: string; value: string}) => {
			setState(prev => ({...prev, setupProfile: item.value === 'advanced' ? 'advanced' : 'safe'}));
			setStep('confirm');
		},
		[]
	);

	useEffect(() => {
		if (step !== 'installing') return;
		const provider = state.provider;
		const model = state.model ?? provider?.defaultModel;
		if (!provider || !model) {
			setState(prev => ({...prev, error: 'Missing provider/model'}));
			setStep('error');
			return;
		}

		try {
			writeRuntimeSettings(provider, state.apiKey, model, state.setupProfile);
		} catch (e) {
			setState(prev => ({...prev, error: (e as Error).message}));
			setStep('error');
			return;
		}

		setTimeout(() => setStep('starting'), 250);
	}, [step, state.provider, state.model, state.apiKey, state.setupProfile]);

	useEffect(() => {
		if (step !== 'starting') return;
		void (async () => {
			const backendPython = pythonInVenv();
			if (!fs.existsSync(backendPython)) {
				setState(prev => ({
					...prev,
					error: `Backend venv python not found at ${backendPython}. Create it in apps/backend/.venv first.`
				}));
				setStep('error');
				return;
			}

			try {
				spawnDetached(backendPython, ['app.py', '--mode', 'api'], backendDir());
			} catch (e) {
				setState(prev => ({...prev, error: (e as Error).message}));
				setStep('error');
				return;
			}

			const backendReady = await waitForUrl('http://localhost:8000/health', 20000, 500);
			if (!backendReady) {
				setState(prev => ({...prev, error: 'Backend did not become healthy at http://localhost:8000/health within 20 seconds.'}));
				setStep('error');
				return;
			}

			const web = webDir();
			const pkg = path.join(web, 'package.json');

			if (fs.existsSync(pkg)) {
				try {
					spawnDetached('npm', ['run', 'dev'], web);
				} catch {
				}
			}

			const url = await detectWebUiUrl();
			setWebUiUrl(url);
			try {
				await open(url);
			} catch {
			}
			setStep('done');
		})();
	}, [step]);

	return (
		<Box flexDirection="column">
			<Box marginBottom={1}>
				<Text bold>EchoSpeak Onboarding</Text>
			</Box>

			{step === 'provider' && (
				<Box flexDirection="column">
					<Text>Select provider:</Text>
					<SelectInput items={providerItems} onSelect={onSelectProvider} />
				</Box>
			)}

			{step === 'apiKey' && state.provider?.apiKeyEnv && (
				<Box flexDirection="column">
					<Text>Enter {state.provider.apiKeyEnv} (stored in backend runtime settings):</Text>
					<Box>
						<TextInput
							value={apiKeyInput}
							onChange={setApiKeyInput}
							onSubmit={value => {
								setState(prev => ({...prev, apiKey: value}));
								setStep('model');
							}}
							mask="*"
						/>
					</Box>
				</Box>
			)}

			{step === 'model' && state.provider && state.provider.models.length > 0 && (
				<Box flexDirection="column">
					<Text>Select model:</Text>
					<SelectInput
						items={modelItems}
						onSelect={onSelectModel}
						initialIndex={Math.max(0, state.provider.models.indexOf(state.provider.defaultModel))}
					/>
				</Box>
			)}

			{step === 'model' && state.provider && state.provider.models.length === 0 && (
				<Box flexDirection="column">
					<Text>Enter model name (default: {state.provider.defaultModel}):</Text>
					<Box>
						<TextInput
							value={localModelInput}
							onChange={setLocalModelInput}
							onSubmit={value => {
								setState(prev => ({...prev, model: value || state.provider?.defaultModel}));
								setStep('profile');
							}}
						/>
					</Box>
				</Box>
			)}

			{step === 'profile' && state.provider && (
				<Box flexDirection="column">
					<Text>Select setup profile:</Text>
					<SelectInput items={profileItems} onSelect={onSelectProfile} />
				</Box>
			)}

			{step === 'confirm' && state.provider && (
				<Box flexDirection="column">
					<Text>Summary:</Text>
					<Text>Provider: {state.provider.label}</Text>
					<Text>Model: {state.model ?? state.provider.defaultModel}</Text>
					<Text>Profile: {state.setupProfile === 'advanced' ? 'Advanced' : 'Safe'}</Text>
					<Text>Settings path: {backendSettingsPath()}</Text>
					<Box marginTop={1}>
						<Text>
							Press <Text bold>Enter</Text> to save + start servers + open Web UI, or type <Text bold>q</Text> to quit.
						</Text>
					</Box>
					<Box marginTop={1}>
						<TextInput
							value={''}
							onChange={() => {}}
							onSubmit={(value: string) => {
								if (value.trim().toLowerCase() === 'q') {
									exit();
									return;
								}
								setStep('installing');
							}}
						/>
					</Box>
				</Box>
			)}

			{step === 'installing' && (
				<Box flexDirection="column">
					<Text>Saving configuration...</Text>
				</Box>
			)}

			{step === 'starting' && (
				<Box flexDirection="column">
					<Text>Starting backend + validating health + opening browser...</Text>
				</Box>
			)}

			{step === 'done' && (
				<Box flexDirection="column">
					<Text>Done.</Text>
					<Text>Web UI: {webUiUrl || 'http://localhost:5173'}</Text>
					<Text>Backend: http://localhost:8000</Text>
					<Text>Settings: {backendSettingsPath()}</Text>
					<Box marginTop={1}>
						<Text>Press Enter to exit.</Text>
					</Box>
					<Box>
						<TextInput
							value={''}
							onChange={() => {}}
							onSubmit={() => exit()}
						/>
					</Box>
				</Box>
			)}

			{step === 'error' && (
				<Box flexDirection="column">
					<Text color="red">Error: {state.error ?? 'Unknown error'}</Text>
					<Box marginTop={1}>
						<Text>Press Enter to exit.</Text>
					</Box>
					<Box>
						<TextInput value={''} onChange={() => {}} onSubmit={() => exit()} />
					</Box>
				</Box>
			)}
		</Box>
	);
}

render(<App />);
