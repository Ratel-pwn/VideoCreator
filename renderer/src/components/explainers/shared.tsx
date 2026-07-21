export const progress = (frame: number, start: number, duration = 16) => Math.max(0, Math.min(1, (frame - start) / duration));
export const explainerFont = 'Noto Sans SC, Microsoft YaHei, sans-serif';
