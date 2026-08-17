import { useCallback, useEffect, useState, type SetStateAction } from "react";
import { usePersistedWidgetState } from "./use-openai-global";

export function useWidgetState<T extends Record<string, unknown>>(
  defaultState: T | (() => T)
): readonly [T, (state: SetStateAction<T>) => void] {
  
  const widgetStateFromWindow = usePersistedWidgetState() as T | null;

  
  const [widgetState, _setWidgetState] = useState<T>(() => {
    if (widgetStateFromWindow) {
      return widgetStateFromWindow;
    }
    return typeof defaultState === "function" ? defaultState() : defaultState;
  });

  
  useEffect(() => {
    if (widgetStateFromWindow) {
      _setWidgetState(widgetStateFromWindow);
    }
  }, [widgetStateFromWindow]);

  
  const setWidgetState = useCallback((state: SetStateAction<T>) => {
    _setWidgetState((prev) => {
      const newState = typeof state === "function" ? state(prev) : state;

      
      window.openai?.setWidgetState?.(newState).catch(console.error);

      return newState;
    });
  }, []);

  return [widgetState, setWidgetState] as const;
}
