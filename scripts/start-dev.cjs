const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const backendHealth = "http://127.0.0.1:8001/api/health";
const frontendUrl = "http://127.0.0.1:5173";
const pythonExe = path.join(root, ".venv", "Scripts", "python.exe");
const frontendDir = path.join(root, "frontend");
const backendEnv = { ...process.env, MECHCAD_RELOAD: "0" };

function exists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

async function isReady(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
    return response.ok;
  } catch {
    return false;
  }
}

function runProcess(name, command, args, cwd, extraEnv = {}) {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...extraEnv },
    stdio: "inherit",
    windowsHide: true,
    shell: false,
  });

  child.on("exit", (code, signal) => {
    if (shutdown.inProgress) {
      return;
    }
    console.log(`[${name}] exited with ${signal || code}`);
    shutdown(`child ${name} stopped`);
  });

  return child;
}

const children = [];

function shutdown(reason) {
  if (shutdown.inProgress) {
    return;
  }
  shutdown.inProgress = true;
  if (reason) {
    console.log(`Shutting down: ${reason}`);
  }
  for (const child of children) {
    try {
      child.kill();
    } catch {
      // Ignore kill errors during shutdown.
    }
  }
  setTimeout(() => process.exit(0), 150);
}
shutdown.inProgress = false;

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

(async () => {
  if (!exists(pythonExe)) {
    console.error(`Cannot find Python at ${pythonExe}`);
    process.exit(1);
  }

  console.log("MechCAD launcher");
  console.log(`  backend: ${backendHealth}`);
  console.log(`  frontend: ${frontendUrl}`);

  if (!(await isReady(backendHealth))) {
    console.log("Starting backend...");
    children.push(runProcess("backend", pythonExe, ["-m", "backend.main"], root, backendEnv));
    let ready = false;
    for (let i = 0; i < 60; i += 1) {
      if (await isReady(backendHealth)) {
        ready = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!ready) {
      console.error("Backend did not become ready on http://127.0.0.1:8001");
      shutdown("backend not ready");
      return;
    }
    console.log("Backend ready.");
  } else {
    console.log("Backend already running.");
  }

  const frontendCommand =
    process.platform === "win32"
      ? { command: process.env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", "npm.cmd run dev -- --host 127.0.0.1"] }
      : { command: "npm", args: ["run", "dev", "--", "--host", "127.0.0.1"] };
  if (!(await isReady(frontendUrl))) {
    console.log("Starting frontend...");
    children.push(runProcess("frontend", frontendCommand.command, frontendCommand.args, frontendDir));
    let ready = false;
    for (let i = 0; i < 60; i += 1) {
      if (await isReady(frontendUrl)) {
        ready = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!ready) {
      console.error("Frontend did not become ready on http://127.0.0.1:5173");
      shutdown("frontend not ready");
      return;
    }
    console.log("Frontend ready.");
  } else {
    console.log("Frontend already running.");
  }

  console.log(`Ready: ${frontendUrl}`);
})().catch((error) => {
  console.error(error);
  shutdown("launcher error");
});
