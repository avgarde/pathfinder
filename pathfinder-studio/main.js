const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');

let mainWindow;
let serverProcess = null;
let serverPort = 9720;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
    backgroundColor: '#1e1e2e'
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // Open DevTools in dev mode
  const isDev = process.argv.includes('--dev');
  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
    stopServer();
  });
}

app.on('ready', createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

app.setAboutPanelOptions({
  applicationName: 'Pathfinder Studio',
  applicationVersion: '0.1.0',
  copyright: 'Pathfinder Studio'
});

// IPC Handlers

ipcMain.handle('get-default-config', async () => {
  return {
    url: '',
    appName: 'Untitled Exploration',
    maxActions: 30
  };
});

ipcMain.handle('start-server', async (event, port) => {
  if (serverProcess) {
    return { success: false, error: 'Server already running' };
  }

  serverPort = port || 9720;

  return new Promise((resolve) => {
    try {
      const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';
      serverProcess = spawn(pythonCommand, [
        '-m',
        'pathfinder.server',
        '--port',
        String(serverPort)
      ]);

      let ready = false;

      serverProcess.stdout.on('data', (data) => {
        console.log(`[pathfinder] ${data}`);
        if (!ready && data.toString().includes('listening')) {
          ready = true;
          resolve({ success: true, port: serverPort });
        }
      });

      serverProcess.stderr.on('data', (data) => {
        console.error(`[pathfinder] ${data}`);
      });

      serverProcess.on('error', (err) => {
        console.error('Failed to start pathfinder server:', err);
        serverProcess = null;
        if (!ready) {
          resolve({ success: false, error: err.message });
        }
      });

      serverProcess.on('exit', (code) => {
        console.log(`[pathfinder] Server exited with code ${code}`);
        serverProcess = null;
      });

      // Timeout if server doesn't start within 10 seconds
      setTimeout(() => {
        if (!ready) {
          resolve({ success: true, port: serverPort });
          ready = true;
        }
      }, 10000);
    } catch (err) {
      console.error('Error spawning server:', err);
      resolve({ success: false, error: err.message });
    }
  });
});

ipcMain.handle('stop-server', async () => {
  return stopServer();
});

function stopServer() {
  if (serverProcess) {
    try {
      serverProcess.kill();
      serverProcess = null;
      return { success: true };
    } catch (err) {
      console.error('Error stopping server:', err);
      return { success: false, error: err.message };
    }
  }
  return { success: true };
}

// Clean up on app quit
app.on('before-quit', () => {
  stopServer();
});
