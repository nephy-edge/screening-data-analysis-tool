// Starts the FastAPI backend (web_api/main.py) and the Vite dev server
// together, so `npm run dev` is the only command needed. Kills both when
// either exits or on Ctrl+C.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';

const repoRoot = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const venvPython = `${repoRoot}\\.venv\\Scripts\\python.exe`;
const pythonCmd = existsSync(venvPython) ? venvPython : 'python';

const backend = spawn(
  pythonCmd,
  ['-m', 'uvicorn', 'web_api.main:app', '--reload', '--port', '8000'],
  { cwd: repoRoot, stdio: 'inherit', shell: true },
);
const frontend = spawn('npx', ['vite'], { stdio: 'inherit', shell: true });

// With shell:true, child.kill() on Windows stops only cmd.exe and leaves
// uvicorn's --reload worker holding port 8000; taskkill /T takes the tree.
const kill = (child) => {
  if (child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  } else {
    child.kill();
  }
};
const shutdown = () => {
  kill(backend);
  kill(frontend);
};
for (const child of [backend, frontend]) {
  child.on('exit', (code) => {
    shutdown();
    process.exit(code ?? 0);
  });
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
