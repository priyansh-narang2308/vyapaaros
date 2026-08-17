"use client";

import { createContext, useContext, useReducer, useCallback, useMemo, type ReactNode } from "react";
import type {
  AgentType,
  AgentActivityStatus,
  AgentActivityEvent,
  PostPurchaseInputSignals,
  PostPurchaseDecision,
  RecommendationInputSignals,
  RecommendationDecision,
  AgentInputSignals,
  AgentDecision,
} from "@/types";

interface AgentActivityLogState {
  events: AgentActivityEvent[];
  isActive: boolean;
}

type AgentActivityLogAction =
  | { type: "ADD_EVENT"; event: AgentActivityEvent }
  | { type: "UPDATE_EVENT"; id: string; updates: Partial<AgentActivityEvent> }
  | { type: "CLEAR" }
  | { type: "SET_ACTIVE"; isActive: boolean };

const initialState: AgentActivityLogState = {
  events: [],
  isActive: false,
};

function agentActivityLogReducer(
  state: AgentActivityLogState,
  action: AgentActivityLogAction
): AgentActivityLogState {
  switch (action.type) {
    case "ADD_EVENT":
      return {
        ...state,
        events: [...state.events, action.event],
        isActive: true,
      };
    case "UPDATE_EVENT":
      return {
        ...state,
        events: state.events.map((event) =>
          event.id === action.id ? { ...event, ...action.updates } : event
        ),
      };
    case "CLEAR":
      return initialState;
    case "SET_ACTIVE":
      return { ...state, isActive: action.isActive };
    default:
      return state;
  }
}

interface AgentActivityLogContextType {
  state: AgentActivityLogState;
    logAgentCall: (agentType: AgentType, inputSignals: AgentInputSignals) => string;
    completeAgentCall: (
    id: string,
    status: AgentActivityStatus,
    decision?: AgentDecision,
    error?: string
  ) => void;
    addAgentEvent: (
    agentType: AgentType,
    inputSignals: AgentInputSignals,
    decision: AgentDecision | undefined,
    status: AgentActivityStatus
  ) => void;
    addPostPurchaseEvent: (
    inputSignals: PostPurchaseInputSignals,
    decision: PostPurchaseDecision | undefined,
    status: AgentActivityStatus,
    error?: string
  ) => void;
    addRecommendationEvent: (
    inputSignals: RecommendationInputSignals,
    decision: RecommendationDecision | undefined,
    status: AgentActivityStatus,
    error?: string
  ) => void;
    clear: () => void;
}

const AgentActivityLogContext = createContext<AgentActivityLogContextType | null>(null);

export function AgentActivityLogProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(agentActivityLogReducer, initialState);

  const logAgentCall = useCallback((agentType: AgentType, inputSignals: AgentInputSignals) => {
    const id = `agent_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
    const event: AgentActivityEvent = {
      id,
      timestamp: new Date(),
      status: "pending",
      agentType,
      inputSignals,
    };
    dispatch({ type: "ADD_EVENT", event });
    return id;
  }, []);

  const completeAgentCall = useCallback(
    (id: string, status: AgentActivityStatus, decision?: AgentDecision, error?: string) => {
      const timestampStr = id.split("_")[1];
      const startTime = timestampStr ? parseInt(timestampStr, 10) : Date.now();
      const duration = Date.now() - startTime;

      const updates: Partial<AgentActivityEvent> = { status, duration };
      if (decision !== undefined) {
        updates.decision = decision;
      }
      if (error !== undefined) {
        updates.error = error;
      }

      dispatch({
        type: "UPDATE_EVENT",
        id,
        updates,
      });
    },
    []
  );

  const addAgentEvent = useCallback(
    (
      agentType: AgentType,
      inputSignals: AgentInputSignals,
      decision: AgentDecision | undefined,
      status: AgentActivityStatus
    ) => {
      const id = `agent_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
      const event: AgentActivityEvent = {
        id,
        timestamp: new Date(),
        status,
        agentType,
        inputSignals,
      };
      
      if (decision !== undefined) {
        event.decision = decision;
      }
      dispatch({ type: "ADD_EVENT", event });
    },
    []
  );

  const addPostPurchaseEvent = useCallback(
    (
      inputSignals: PostPurchaseInputSignals,
      decision: PostPurchaseDecision | undefined,
      status: AgentActivityStatus,
      error?: string
    ) => {
      const id = `agent_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
      const event: AgentActivityEvent = {
        id,
        timestamp: new Date(),
        status,
        agentType: "post_purchase",
        inputSignals,
      };
      if (decision !== undefined) {
        event.decision = decision;
      }
      if (error !== undefined) {
        event.error = error;
      }
      dispatch({ type: "ADD_EVENT", event });
    },
    []
  );

  const addRecommendationEvent = useCallback(
    (
      inputSignals: RecommendationInputSignals,
      decision: RecommendationDecision | undefined,
      status: AgentActivityStatus,
      error?: string
    ) => {
      const id = `agent_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
      const event: AgentActivityEvent = {
        id,
        timestamp: new Date(),
        status,
        agentType: "recommendation",
        inputSignals,
      };
      if (decision !== undefined) {
        event.decision = decision;
      }
      if (error !== undefined) {
        event.error = error;
      }
      dispatch({ type: "ADD_EVENT", event });
    },
    []
  );

  const clear = useCallback(() => {
    dispatch({ type: "CLEAR" });
  }, []);

  
  const contextValue = useMemo(
    () => ({
      state,
      logAgentCall,
      completeAgentCall,
      addAgentEvent,
      addPostPurchaseEvent,
      addRecommendationEvent,
      clear,
    }),
    [
      state,
      logAgentCall,
      completeAgentCall,
      addAgentEvent,
      addPostPurchaseEvent,
      addRecommendationEvent,
      clear,
    ]
  );

  return (
    <AgentActivityLogContext.Provider value={contextValue}>
      {children}
    </AgentActivityLogContext.Provider>
  );
}

export function useAgentActivityLog() {
  const context = useContext(AgentActivityLogContext);
  if (!context) {
    throw new Error("useAgentActivityLog must be used within AgentActivityLogProvider");
  }
  return context;
}
