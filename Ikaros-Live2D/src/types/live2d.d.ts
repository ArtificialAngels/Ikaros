// Type declarations for pixi-live2d-display
declare module 'pixi-live2d-display' {
  import * as PIXI from 'pixi.js';

  export class Live2DModel extends PIXI.Container {
    static from(source: string, options?: any): Promise<Live2DModel>;
    internalModel: {
      coreModel: {
        _model: {
          parameters: {
            ids: string[];
            count: number;
            values: Float32Array;
            defaultValues: Float32Array;
          };
        };
      };
    };
    anchor: PIXI.ObservablePoint;
    motion(group: string, index: number): void;
    expression(name: string): void;
    hitTest(x: number, y: number): string[];
    destroy(options?: any): void;
  }

  export function registerTicker(ticker: typeof PIXI.Ticker): Promise<void>;
}
