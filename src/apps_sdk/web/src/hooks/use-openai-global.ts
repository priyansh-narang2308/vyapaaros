import { useSyncExternalStore } from "react";
import type { OpenAiGlobals } from "@/types";

const SET_GLOBALS_EVENT_TYPE = "openai:set_globals";

type OpenAiGlobalKey =
  | "theme"
  | "locale"
  | "maxHeight"
  | "displayMode"
  | "toolInput"
  | "toolOutput"
  | "widgetState";

export function useOpenAiGlobal<K extends OpenAiGlobalKey>(
  key: K
): OpenAiGlobals[K] | null {
  return useSyncExternalStore(
    
    (onChange) => {
      if (typeof window === "undefined") return () => {};

      const handleSetGlobal = (
        event: CustomEvent<{ globals: Partial<OpenAiGlobals> }>
      ) => {
        if (event.detail.globals[key] !== undefined) {
          onChange();
        }
      };

      window.addEventListener(
        SET_GLOBALS_EVENT_TYPE,
        handleSetGlobal as EventListener
      );

      return () => {
        window.removeEventListener(
          SET_GLOBALS_EVENT_TYPE,
          handleSetGlobal as EventListener
        );
      };
    },
    
    () => window.openai?.[key] ?? null,
    
    () => null
  );
}


export const useTheme = () => useOpenAiGlobal("theme");
export const useLocale = () => useOpenAiGlobal("locale");
export const useToolInput = () => useOpenAiGlobal("toolInput");
export const useToolOutput = () => useOpenAiGlobal("toolOutput");
export const useDisplayMode = () => useOpenAiGlobal("displayMode");
export const usePersistedWidgetState = () => useOpenAiGlobal("widgetState");
