/**
 * Live2D Adapter Service
 * Wraps PixiJS + pixi-live2d-display for Live2D Cubism 4 rendering.
 * Mirrors exProject/Live2DPet/src/renderer/model-adapter.js Live2DAdapter.
 *
 * IMPORTANT: pixi-live2d-display must be dynamically imported AFTER
 * Cubism Core is loaded globally. Static imports are bundled by Vite
 * before the <script> injection in main.ts completes.
 */

export interface ParamMapping {
  angleX: string
  angleY: string
  angleZ: string
  bodyAngleX: string
  eyeBallX: string
  eyeBallY: string
}

export const DEFAULT_PARAM_MAPPING: ParamMapping = {
  angleX: 'ParamAngleX',
  angleY: 'ParamAngleY',
  angleZ: 'ParamAngleZ',
  bodyAngleX: 'ParamBodyAngleX',
  eyeBallX: 'ParamEyeBallX',
  eyeBallY: 'ParamEyeBallY',
}

export class Live2DAdapter {
  private pixiApp: any = null
  private model: any = null
  private paramMap: Record<string, number> = {}
  private canvas: HTMLCanvasElement | null = null

  // Tracking state
  private _trackX = 0
  private _trackY = 0
  private paramMapping: ParamMapping = DEFAULT_PARAM_MAPPING
  private modelScale = 1.0

  // Model list management
  private _modelList: { name: string; path: string }[] = []
  private _currentModelIndex = 0
  private _hitFramesVisible = false
  private _hitFrameGraphics: any = null

  // Expression system
  private _expressionCache: Record<string, Array<{ Id: string; Value: number }>> = {}
  private _activeExprParams: Array<{ Id: string; Value: number }> | null = null
  private _savedParamDefaults: Record<string, number> | null = null
  private _modelDir: string | null = null

  get isLoaded(): boolean {
    return this.model !== null
  }

  get rawModel(): any {
    return this.model
  }

  /**
   * Initialize PixiJS Application on the given canvas.
   * Dynamically imports pixi-live2d-display after Cubism Core is ready.
   */
  async init(canvasEl: HTMLCanvasElement): Promise<void> {
    this.canvas = canvasEl
    console.log('[Live2DAdapter] init start, canvas:', canvasEl.width, 'x', canvasEl.height)

    // Check if Cubism Core is loaded
    console.log('[Live2DAdapter] Checking Cubism Core...')
    console.log('[Live2DAdapter] window.Live2DCubismCore:', !!(window as any).Live2DCubismCore)
    console.log('[Live2DAdapter] window.Live2D:', !!(window as any).Live2D)

    // Dynamic import — must happen AFTER Cubism Core <script> is loaded
    console.log('[Live2DAdapter] importing pixi.js...')
    const PIXI = await import('pixi.js')
    console.log('[Live2DAdapter] pixi.js loaded, version:', PIXI.VERSION)

    console.log('[Live2DAdapter] importing pixi-live2d-display...')
    const { Live2DModel } = await import('pixi-live2d-display')
    console.log('[Live2DAdapter] pixi-live2d-display loaded')

    // Expose PIXI globally for pixi-live2d-display
    ;(window as any).PIXI = PIXI

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    console.log('[Live2DAdapter] creating PIXI.Application...')
    this.pixiApp = new PIXI.Application({
      view: canvasEl,
      transparent: true,
      autoStart: true,
      width: window.innerWidth,
      height: window.innerHeight,
      backgroundAlpha: 0,
      resolution: dpr,
      autoDensity: true,
      powerPreference: 'high-performance',
      antialias: true,
    })
    console.log('[Live2DAdapter] PIXI.Application created')

    // Store Live2DModel class for later use
    ;(this as any)._Live2DModel = Live2DModel

    // Apply tracking every frame — use ticker.add(..., PIXI.UPDATE_PRIORITY.LOW)
    // to run AFTER the model's internal update
    this.pixiApp.ticker.add(() => {
      if (!this.model) return
      this._applyTracking()
      this._applyExpression()
      if (this._hitFramesVisible && this._hitFrameGraphics) {
        this._updateHitFrameGraphics()
      }
    })
    console.log('[Live2DAdapter] init complete')
  }

  /**
   * Load a Live2D model from the given path.
   */
  async loadModel(modelPath: string, scale = 1.0): Promise<void> {
    if (!this.pixiApp) throw new Error('PixiJS not initialized')

    const Live2DModel = (this as any)._Live2DModel
    this.modelScale = scale
    console.log('[Live2DAdapter] loading model:', modelPath)
    try {
      this.model = await Live2DModel.from(modelPath, {
        autoUpdate: true,
        autoInteract: false,
      })
      console.log('[Live2DAdapter] model instance created')
    } catch (e) {
      console.error('[Live2DAdapter] Live2DModel.from() failed:', e)
      throw e
    }

    // Store model directory for expression file loading
    const lastSlash = modelPath.lastIndexOf('/')
    this._modelDir = lastSlash >= 0 ? modelPath.substring(0, lastSlash) : modelPath

    this.model.anchor.set(0.5, 0.5)
    ;(this.model as any)._origW = this.model.width
    ;(this.model as any)._origH = this.model.height
    this.pixiApp.stage.addChild(this.model)

    this._fitModel()
    this._buildParamMap()
    this._loadExpressionFiles()

    console.log('[Live2DAdapter] Model loaded:', modelPath)
  }

  /**
   * Set a model parameter by name.
   * Uses Cubism Core's setParameterValue API instead of raw array access.
   */
  setParam(name: string, value: number): void {
    if (!this.model) return
    const idx = this.paramMap[name]
    if (idx === undefined) return
    try {
      const core = this.model.internalModel.coreModel
      // Use the Cubism Core API — more reliable than raw _model access
      if (typeof core.setParameterValueByIndex === 'function') {
        core.setParameterValueByIndex(idx, value)
      } else if (typeof core.setParameterValue === 'function') {
        core.setParameterValue(name, value)
      } else {
        // Fallback: direct array access (same as exProject/Live2DPet model-adapter.js)
        core._model.parameters.values[idx] = value
      }
    } catch (_e) {
      // param out of range, ignore
    }
  }

  /**
   * Play a motion by group and index.
   */
  playMotion(group: string, index: number): void {
    if (!this.model) return
    try {
      this.model.motion(group, index)
      console.log(`[Live2DAdapter] motion: ${group}[${index}]`)
    } catch (e) {
      console.warn('[Live2DAdapter] playMotion failed:', e)
    }
  }

  /**
   * Hit test at the given coordinates.
   */
  hitTest(x: number, y: number): string[] {
    if (!this.model) return []
    try {
      const areas = this.model.hitTest(x, y)
      return areas || []
    } catch (_e) {
      return []
    }
  }

  /**
   * Update tracking coordinates (called from mouse tracking).
   */
  updateTracking(trackX: number, trackY: number): void {
    this._trackX = trackX
    this._trackY = trackY
  }

  /**
   * Resize the renderer and refit the model.
   */
  resize(width: number, height: number): void {
    if (!this.pixiApp) return
    this.pixiApp.renderer.resize(width, height)
    this._fitModel()
  }

  /**
   * Set the model list for switching.
   */
  setModelList(list: { name: string; path: string }[], currentIndex = 0): void {
    this._modelList = list
    this._currentModelIndex = currentIndex
  }

  /**
   * Get all model names.
   */
  getAllModelNames(): string[] {
    return this._modelList.map(m => m.name)
  }

  /**
   * Get current model name.
   */
  getCurrentModelName(): string {
    if (this._modelList.length === 0) return 'Unknown'
    return this._modelList[this._currentModelIndex]?.name || 'Unknown'
  }

  /**
   * Get current model index.
   */
  getCurrentModelIndex(): number {
    return this._currentModelIndex
  }

  /**
   * Get model count.
   */
  getModelCount(): number {
    return this._modelList.length
  }

  /**
   * Switch to next model.
   */
  async nextModel(): Promise<void> {
    if (this._modelList.length <= 1) return
    const nextIdx = (this._currentModelIndex + 1) % this._modelList.length
    await this._switchToModel(nextIdx)
  }

  /**
   * Switch to previous model.
   */
  async prevModel(): Promise<void> {
    if (this._modelList.length <= 1) return
    const prevIdx = (this._currentModelIndex - 1 + this._modelList.length) % this._modelList.length
    await this._switchToModel(prevIdx)
  }

  /**
   * Switch to a specific model by index.
   */
  async switchToModel(index: number): Promise<void> {
    if (index < 0 || index >= this._modelList.length || index === this._currentModelIndex) return
    await this._switchToModel(index)
  }

  private async _switchToModel(index: number): Promise<void> {
    if (!this.pixiApp) return
    const entry = this._modelList[index]
    if (!entry) return
    console.log('[Live2DAdapter] switching to model:', entry.name, entry.path)
    // Remove current model
    if (this.model) {
      this._cleanupHitFrames()
      this.pixiApp.stage.removeChild(this.model)
      this.model.destroy()
      this.model = null
    }
    // Load new model
    try {
      const Live2DModel = (this as any)._Live2DModel
      this.model = await Live2DModel.from(entry.path, {
        autoUpdate: true,
        autoInteract: false,
      })
      this.model.anchor.set(0.5, 0.5)
      ;(this.model as any)._origW = this.model.width
      ;(this.model as any)._origH = this.model.height
      this.pixiApp.stage.addChild(this.model)
      this._currentModelIndex = index
      this.modelScale = 1.0
      this._fitModel()
      this._buildParamMap()
      console.log('[Live2DAdapter] model switched:', entry.name)
    } catch (e) {
      console.error('[Live2DAdapter] switch model failed:', e)
    }
  }

  /**
   * Switch to next texture/costume for current model.
   */
  nextTexture(): void {
    if (!this.model) return
    try {
      const internal = this.model.internalModel
      if (internal && internal.settings && internal.settings.textures) {
        const textures = internal.settings.textures
        if (textures.length > 1) {
          console.log('[Live2DAdapter] nextTexture: textures count =', textures.length)
        }
      }
      console.log('[Live2DAdapter] nextTexture called')
    } catch (e) {
      console.warn('[Live2DAdapter] nextTexture failed:', e)
    }
  }

  /**
   * Set model scale multiplier.
   */
  setModelScale(multiplier: number): void {
    this.modelScale = multiplier
    this._fitModel()
  }

  /**
   * Get current model scale.
   */
  getModelScale(): number {
    return this.modelScale
  }

  /**
   * Toggle hit area frame visibility.
   * Creates a simple PIXI Graphics overlay — no text objects to avoid leaks.
   */
  toggleHitFrames(): boolean {
    this._hitFramesVisible = !this._hitFramesVisible
    if (!this.model || !this.pixiApp) return this._hitFramesVisible

    const PIXI = (window as any).PIXI
    if (!PIXI) return this._hitFramesVisible

    // Remove existing debug graphics
    if (this._hitFrameGraphics) {
      this.model.removeChild(this._hitFrameGraphics)
      this._hitFrameGraphics.destroy()
      this._hitFrameGraphics = null
    }

    if (this._hitFramesVisible) {
      this._hitFrameGraphics = new PIXI.Graphics()
      this.model.addChild(this._hitFrameGraphics)
      console.log('[Live2DAdapter] Hit frame debug: ON')
    } else {
      console.log('[Live2DAdapter] Hit frame debug: OFF')
    }

    return this._hitFramesVisible
  }

  /**
   * Redraw hit frame overlay every frame.
   * Uses Cubism Core drawable API to get vertex positions and compute bounding boxes.
   */
  private _updateHitFrameGraphics(): void {
    if (!this._hitFrameGraphics || !this.model) return
    const g = this._hitFrameGraphics
    g.clear()

    try {
      const internal = this.model.internalModel
      const settings = internal?.settings
      const coreModel = internal?.coreModel
      if (!settings || !coreModel) return

      const mw = (this.model as any)._origW || this.model.width
      const mh = (this.model as any)._origH || this.model.height

      // Model bounding box (green) — canvas coords: (0,0) top-left
      g.lineStyle(2, 0x00ff00, 0.7)
      g.drawRect(0, 0, mw, mh)

      // Center crosshair (canvas center)
      g.lineStyle(1, 0x00ff00, 0.5)
      const cx = mw / 2, cy = mh / 2
      g.moveTo(cx - 15, cy); g.lineTo(cx + 15, cy)
      g.moveTo(cx, cy - 15); g.lineTo(cx, cy + 15)

      // Hit areas from Cubism Core drawable vertex positions
      if (settings.hitAreas && typeof coreModel.getDrawableCount === 'function') {
        for (const ha of settings.hitAreas) {
          const drawableId = ha.id || ha.name
          if (!drawableId) continue

          // Get drawable index by ID (this is a string name)
          const idx = coreModel.getDrawableIndex(drawableId)
          if (idx < 0) continue

          // Get vertex positions: Float32Array [x0, y0, x1, y1, ...]
          const vertices = coreModel.getDrawableVertexPositions(idx)
          if (!vertices || vertices.length < 4) continue

          // Compute bounding box in canvas coordinates (Y-down, origin top-left)
          let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
          for (let i = 0; i < vertices.length; i += 2) {
            const vx = vertices[i]
            const vy = vertices[i + 1]
            if (vx < minX) minX = vx
            if (vy < minY) minY = vy
            if (vx > maxX) maxX = vx
            if (vy > maxY) maxY = vy
          }

          const bw = maxX - minX
          const bh = maxY - minY
          if (bw < 1 || bh < 1) continue

          // Filled bounding box (semi-transparent)
          g.lineStyle(2, 0xff4444, 0.9)
          g.beginFill(0xff4444, 0.15)
          g.drawRect(minX, minY, bw, bh)
          g.endFill()

          // Label background
          const label = drawableId
          g.lineStyle(0)
          g.beginFill(0xff4444, 0.7)
          g.drawRect(minX, minY - 14, Math.min(bw, label.length * 8 + 6), 14)
          g.endFill()
        }
      }
    } catch (_e) {
      // ignore per-frame errors
    }
  }

  /**
   * Get hit frames visibility state.
   */
  isHitFramesVisible(): boolean {
    return this._hitFramesVisible
  }

  /**
   * Take a screenshot of the canvas.
   */
  screenshot(): string | null {
    if (!this.pixiApp) return null
    try {
      return this.pixiApp.renderer.plugins.extract.canvas(this.pixiApp.stage).toDataURL('image/png')
    } catch {
      return null
    }
  }

  /**
   * Destroy the adapter and clean up resources.
   */
  destroy(): void {
    if (this.model) {
      this._cleanupHitFrames()
      if (this.pixiApp) {
        this.pixiApp.stage.removeChild(this.model)
      }
      this.model.destroy()
      this.model = null
    }
    if (this.pixiApp) {
      this.pixiApp.destroy(false)
      this.pixiApp = null
    }
    this.paramMap = {}
  }

  // ─── Private ───

  private _fitModel(): void {
    if (!this.model || !this.pixiApp) return
    const w = window.innerWidth
    const h = window.innerHeight
    const origW = (this.model as any)._origW || this.model.width
    const scale = (w / origW) * this.modelScale
    this.model.scale.set(scale)
    this.model.x = w / 2
    this.model.y = h * 0.6
  }

  private _buildParamMap(): void {
    try {
      const core = this.model!.internalModel.coreModel
      const m = core._model
      this.paramMap = {}
      for (let i = 0; i < m.parameters.count; i++) {
        this.paramMap[m.parameters.ids[i]] = i
      }
      console.log('[Live2DAdapter] ParamMap built:', Object.keys(this.paramMap).length, 'params')
      // Log first few params for debugging
      const keys = Object.keys(this.paramMap).slice(0, 6)
      console.log('[Live2DAdapter] First params:', keys.map(k => `${k}=${this.paramMap[k]}`).join(', '))
    } catch (e: any) {
      console.error('[Live2DAdapter] Failed to build param map:', e?.message || e)
    }
  }

  private _applyTracking(): void {
    const pm = this.paramMapping
    if (!pm) return
    // Apply with the same multipliers as the old exProject adapter
    if (pm.angleX) this.setParam(pm.angleX, this._trackX * 30)
    if (pm.angleY) this.setParam(pm.angleY, -this._trackY * 30)
    if (pm.angleZ) this.setParam(pm.angleZ, this._trackX * -5)
    if (pm.bodyAngleX) this.setParam(pm.bodyAngleX, this._trackX * 8)
    if (pm.eyeBallX) this.setParam(pm.eyeBallX, this._trackX)
    if (pm.eyeBallY) this.setParam(pm.eyeBallY, -this._trackY)
  }

  private _applyExpression(): void {
    if (!this._activeExprParams) return
    for (const p of this._activeExprParams) {
      this.setParam(p.Id, p.Value)
    }
  }

  /**
   * Load expression files (.exp3.json) from the model directory.
   */
  private async _loadExpressionFiles(): Promise<void> {
    if (!this._modelDir) return
    // Clear cache on model reload
    this._expressionCache = {}
    this._activeExprParams = null
    this._savedParamDefaults = null

    try {
      // Try to discover exp3.json files via fetch. They follow naming like:
      // expressions/F01.exp3.json, expressions/F02.exp3.json, etc.
      const knownExprs = ['F01', 'F02', 'F03', 'F04', 'F05', 'F06', 'F07', 'F08']
      for (const id of knownExprs) {
        const url = `${this._modelDir}/expressions/${id}.exp3.json`
        try {
          const resp = await fetch(url)
          if (!resp.ok) continue
          const json = await resp.json()
          if (json.Parameters && Array.isArray(json.Parameters)) {
            this._expressionCache[id] = json.Parameters
          }
        } catch {
          // Not found, skip
        }
      }
      console.log('[Live2DAdapter] Loaded', Object.keys(this._expressionCache).length, 'expressions:',
        Object.keys(this._expressionCache))
    } catch (e) {
      console.warn('[Live2DAdapter] Failed to load expression files:', e)
    }
  }

  // ─── Expression System ───

  /**
   * Set a Live2D expression by name (from exp3.json).
   */
  setExpression(name: string): void {
    if (!this.model) return
    const params = this._expressionCache[name]
    if (!params) {
      console.warn(`[Live2DAdapter] setExpression("${name}") → not found, available:`, Object.keys(this._expressionCache))
      return
    }

    // Save default values for revert
    this._savedParamDefaults = {}
    try {
      const core = this.model.internalModel.coreModel
      const defaults = core._model.parameters.defaultValues
      for (const p of params) {
        const idx = this.paramMap[p.Id]
        if (idx !== undefined) {
          this._savedParamDefaults[p.Id] = defaults[idx]
        }
      }
    } catch (e) {
      for (const p of params) {
        this._savedParamDefaults[p.Id] = 0
      }
    }
    this._activeExprParams = params
    console.log(`[Live2DAdapter] setExpression("${name}") → ${params.length} params`)
  }

  /**
   * Revert expression to default parameter values.
   */
  revertExpression(): void {
    if (!this.model) return
    if (this._savedParamDefaults) {
      for (const [id, val] of Object.entries(this._savedParamDefaults)) {
        this.setParam(id, val)
      }
      console.log('[Live2DAdapter] revertExpression → restored', Object.keys(this._savedParamDefaults).length, 'params')
      this._savedParamDefaults = null
    }
    this._activeExprParams = null
  }

  /**
   * Get list of available expression names.
   */
  getExpressions(): string[] {
    return Object.keys(this._expressionCache)
  }

  /**
   * Clean up hit frame debug graphics.
   */
  private _cleanupHitFrames(): void {
    if (this._hitFrameGraphics && this.model) {
      try {
        this.model.removeChild(this._hitFrameGraphics)
        this._hitFrameGraphics.destroy()
      } catch (_e) { /* ignore */ }
      this._hitFrameGraphics = null
    }
    this._hitFramesVisible = false
  }
}

// ═══════════════════════════════════════════════════════════════
// ImageAdapter — Static image / GIF fallback (no Live2D needed)
// ═══════════════════════════════════════════════════════════════

export interface ImageModelConfig {
  staticImagePath?: string
  imageFolderPath?: string
  imageFiles?: Array<{ file: string; idle?: boolean; talking?: boolean; emotionName?: string }>
  imageCropScale?: number
  gifExpressions?: Record<string, string>
  bottomAlignOffset?: number
}

export class ImageAdapter {
  private _imgElement: HTMLImageElement | null = null
  private _container: HTMLElement | null = null
  private _config: ImageModelConfig = {}

  private _idleImages: string[] = []
  private _talkingImages: string[] = []
  private _emotionImages: Record<string, string[]> = {}
  private _folderMode = false
  private _isTalking = false
  private _currentEmotion: string | null = null

  constructor(config: ImageModelConfig = {}) { this._config = config }

  get isFolderMode(): boolean { return this._folderMode }

  async load(container: HTMLElement): Promise<void> {
    this._container = container
    let img = container.querySelector('#ikaros-static-image') as HTMLImageElement
    if (!img) {
      img = document.createElement('img')
      img.id = 'ikaros-static-image'
      img.style.cssText = 'display:block;position:absolute;pointer-events:none'
      container.appendChild(img)
    }
    this._imgElement = img
    this._folderMode = !!(this._config.imageFolderPath && this._config.imageFiles?.length)
    if (this._folderMode) {
      this._buildPools()
      this._applyCropStyle()
      this._updateDisplay()
    } else if (this._config.staticImagePath) {
      this._imgElement.src = this._config.staticImagePath
      const off = this._config.bottomAlignOffset ?? 0.5
      this._imgElement.style.cssText += `;bottom:${(1 - off) * 100}%;left:50%;transform:translateX(-50%)`
    }
  }

  setExpression(name: string): void {
    if (this._folderMode) { this._currentEmotion = name; this._updateDisplay() }
    else {
      const gif = this._config.gifExpressions?.[name]
      if (gif && this._imgElement) this._imgElement.src = gif
    }
  }

  revertExpression(): void {
    if (this._folderMode) { this._currentEmotion = null; this._updateDisplay() }
    else if (this._imgElement && this._config.staticImagePath) this._imgElement.src = this._config.staticImagePath
  }

  setTalking(t: boolean): void { this._isTalking = t; if (!this._currentEmotion) this._updateDisplay() }
  updateTracking(_x: number, _y: number): void {}
  resize(_w: number, _h: number): void {}

  destroy(): void {
    if (this._imgElement) { this._imgElement.src = ''; this._imgElement.style.display = 'none' }
    this._idleImages = []; this._talkingImages = []; this._emotionImages = {}
  }

  private _buildPools(): void {
    this._idleImages = []; this._talkingImages = []; this._emotionImages = {}
    for (const f of this._config.imageFiles || []) {
      if (f.idle) this._idleImages.push(f.file)
      if (f.talking) this._talkingImages.push(f.file)
      if (f.emotionName) {
        (this._emotionImages[f.emotionName] ??= []).push(f.file)
      }
    }
  }

  private _updateDisplay(): void {
    if (!this._folderMode || !this._imgElement) return
    if (this._currentEmotion && this._emotionImages[this._currentEmotion]?.length)
      this._showRandom(this._emotionImages[this._currentEmotion])
    else if (this._isTalking && this._talkingImages.length)
      this._showRandom(this._talkingImages)
    else if (this._idleImages.length)
      this._showRandom(this._idleImages)
  }

  private _showRandom(pool: string[]): void {
    if (!pool?.length || !this._imgElement) return
    const file = pool[Math.floor(Math.random() * pool.length)]
    const fp = (this._config.imageFolderPath || '').replace(/\\/g, '/')
    this._imgElement.src = `file:///${fp}/${encodeURIComponent(file)}`
  }

  private _applyCropStyle(): void {
    if (!this._imgElement) return
    const s = this._config.imageCropScale ?? 1.0
    this._imgElement.style.cssText += `;top:0;left:0;width:100%;height:auto;transform-origin:top center;transform:scale(${s})`
  }
}
