// ratis_client/services/auth-events.ts

type Listener = () => void;

class AuthEvents {
  private listeners: Set<Listener> = new Set();

  onForceLogout(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emitForceLogout(): void {
    for (const l of this.listeners) l();
  }
}

export const authEvents = new AuthEvents();
