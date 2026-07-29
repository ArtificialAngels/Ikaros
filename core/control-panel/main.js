// Standalone Electron panel for the Ikaros control dashboard (:9100).
// Modeled after N.E.K.O (Electron): frameless window + custom title bar + tray.
// Reuses the host's existing N.E.K.O electron binary (no separate download).
const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..'); // E:\Ikaros
const ICON = path.join(ROOT, 'Artificialangelmini.png');
const PANEL_URL = 'http://127.0.0.1:9100';

const TITLEBAR_CSS = fs.readFileSync(path.join(__dirname, 'titlebar.css'), 'utf-8');
const TITLEBAR_JS = fs.readFileSync(path.join(__dirname, 'titlebar.js'), 'utf-8');

let mainWindow = null;
let tray = null;
let loadAttempts = 0;
let isQuiting = false;

function showWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function toggleTop() {
  if (!mainWindow) return;
  const next = !mainWindow.isAlwaysOnTop();
  mainWindow.setAlwaysOnTop(next);
  mainWindow.webContents.send('panel:top-changed', next);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 880,
    minHeight: 600,
    title: 'Ikaros 控制面板',
    frame: false,
    show: false,
    backgroundColor: '#0f1115',
    autoHideMenuBar: true,
    icon: nativeImage.createFromPath(ICON),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  // Remove the default application menu (no browser-like menu bar).
  Menu.setApplicationMenu(null);

  mainWindow.loadURL(PANEL_URL);

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });

  // Closing the window hides to tray instead of quitting (panel behaviour).
  mainWindow.on('close', (e) => {
    if (!isQuiting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  // Inject the frameless title bar once the page is live.
  mainWindow.webContents.on('did-finish-load', () => {
    loadAttempts = 0;
    injectTitleBar();
  });

  // Backend (:9100) may still be booting when we launch — retry a few times.
  mainWindow.webContents.on('did-fail-load', (e, code) => {
    if ((code === -102 || code === -106) && loadAttempts < 15) {
      loadAttempts++;
      setTimeout(() => mainWindow && mainWindow.loadURL(PANEL_URL), 800);
    }
  });
}

function injectTitleBar() {
  if (!mainWindow) return;
  const wc = mainWindow.webContents;
  wc.insertCSS(TITLEBAR_CSS).catch(() => {});
  wc.executeJavaScript(TITLEBAR_JS).catch(() => {});
}

function createTray() {
  let img = nativeImage.createFromPath(ICON);
  if (img.isEmpty()) img = nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
  );
  tray = new Tray(img);
  tray.setToolTip('Ikaros 控制面板');
  const ctx = Menu.buildFromTemplate([
    { label: '显示控制面板', click: () => showWindow() },
    { label: '隐藏', click: () => mainWindow && mainWindow.hide() },
    { type: 'separator' },
    { label: '刷新', click: () => mainWindow && mainWindow.reload() },
    { label: '置顶切换', click: () => toggleTop() },
    { type: 'separator' },
    { label: '退出', click: () => { isQuiting = true; app.quit(); } },
  ]);
  tray.setContextMenu(ctx);
  tray.on('click', () => showWindow());
}

// --- IPC from the injected title bar ---
ipcMain.on('panel:minimize', () => mainWindow && mainWindow.minimize());
ipcMain.on('panel:close', () => mainWindow && mainWindow.close());
ipcMain.on('panel:toggle-top', () => toggleTop());
ipcMain.handle('panel:is-top', () => !!(mainWindow && mainWindow.isAlwaysOnTop()));

// --- Single instance: focus existing window instead of opening a second ---
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => showWindow());
  app.whenReady().then(() => {
    createWindow();
    createTray();
  });
  app.on('before-quit', () => { isQuiting = true; });
  // Keep running in tray after the window is closed.
  app.on('window-all-closed', () => {});
}
