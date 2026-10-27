import { create } from "zustand";

interface UiState {
  assistantOpen: boolean;
  assistantPrompt: string | null;
  openAssistant: (prompt?: string) => void;
  closeAssistant: () => void;
  /** Request id to replay on the Architecture page (set from feeds elsewhere). */
  replayRequest: string | null;
  setReplayRequest: (id: string | null) => void;
}

export const useUi = create<UiState>((set) => ({
  assistantOpen: false,
  assistantPrompt: null,
  openAssistant: (prompt) => set({ assistantOpen: true, assistantPrompt: prompt ?? null }),
  closeAssistant: () => set({ assistantOpen: false }),
  replayRequest: null,
  setReplayRequest: (id) => set({ replayRequest: id }),
}));
