export type MobileCharacter = { id: string; name: string; prompt: string; createdAt: number; desktopCard?: { description?: string; avatar_data?: string } };
export type MobileConversation = { id: string; characterId: string; title: string; updatedAt: number };
export type MobileMessage = { id: string; conversationId: string; role: "user" | "assistant"; content: string; createdAt: number };
export type MobileSettings = { id: "model"; baseUrl: string; model: string; temperature: number; maxTokens: number; activeProfileId?: string };
export type MobileModelProfile = { id: string; name: string; baseUrl: string; model: string; visionModel: string; createdAt: number };
export type MobileInstruction = { id: string; characterId: string; conversationId: string | null; content: string; enabled: boolean; createdAt: number; sourceTemplateName?: string };
export type MobilePromptTemplate = { id: string; name: string; category: string; content: string; createdAt: number };

type StoreName = "characters" | "conversations" | "messages" | "settings" | "modelProfiles" | "instructions" | "promptTemplates";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("yus-ai-mobile", 3);
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const name of ["characters", "conversations", "messages", "settings", "modelProfiles", "instructions", "promptTemplates"]) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function query<T>(storeName: StoreName, mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(storeName, mode);
    const request = action(transaction.objectStore(storeName));
    let result: T;
    request.onsuccess = () => { result = request.result; };
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => { db.close(); resolve(result); };
    transaction.onerror = () => { db.close(); reject(transaction.error); };
    transaction.onabort = () => { db.close(); reject(transaction.error); };
  });
}

export const mobileDb = {
  async characters() { return (await query<MobileCharacter[]>("characters", "readonly", (store) => store.getAll())).sort((a, b) => a.createdAt - b.createdAt); },
  async conversations(characterId: string) { return (await query<MobileConversation[]>("conversations", "readonly", (store) => store.getAll())).filter((item) => item.characterId === characterId).sort((a, b) => b.updatedAt - a.updatedAt); },
  async messages(conversationId: string) { return (await query<MobileMessage[]>("messages", "readonly", (store) => store.getAll())).filter((item) => item.conversationId === conversationId).sort((a, b) => a.createdAt - b.createdAt); },
  async settings() { return (await query<MobileSettings | undefined>("settings", "readonly", (store) => store.get("model"))) ?? { id: "model" as const, baseUrl: "", model: "", temperature: 0.8, maxTokens: 2048 }; },
  async modelProfiles() { return (await query<MobileModelProfile[]>("modelProfiles", "readonly", (store) => store.getAll())).sort((a, b) => a.createdAt - b.createdAt); },
  async instructions(characterId: string, conversationId: string | null) { return (await query<MobileInstruction[]>("instructions", "readonly", (store) => store.getAll())).filter((item) => item.characterId === characterId && (!item.conversationId || item.conversationId === conversationId)).sort((a, b) => Number(Boolean(a.conversationId)) - Number(Boolean(b.conversationId)) || a.createdAt - b.createdAt); },
  async instructionCount(characterId: string) { return (await query<MobileInstruction[]>("instructions", "readonly", (store) => store.getAll())).filter((item) => item.characterId === characterId).length; },
  async promptTemplates() { return (await query<MobilePromptTemplate[]>("promptTemplates", "readonly", (store) => store.getAll())).sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)); },
  async putCharacter(item: MobileCharacter) { await query("characters", "readwrite", (store) => store.put(item)); },
  async putConversation(item: MobileConversation) { await query("conversations", "readwrite", (store) => store.put(item)); },
  async putMessage(item: MobileMessage) { await query("messages", "readwrite", (store) => store.put(item)); },
  async putSettings(item: MobileSettings) { await query("settings", "readwrite", (store) => store.put(item)); },
  async putModelProfile(item: MobileModelProfile) { await query("modelProfiles", "readwrite", (store) => store.put(item)); },
  async putInstruction(item: MobileInstruction) { await query("instructions", "readwrite", (store) => store.put(item)); },
  async deleteInstruction(id: string) { await query("instructions", "readwrite", (store) => store.delete(id)); },
  async putPromptTemplate(item: MobilePromptTemplate) { await query("promptTemplates", "readwrite", (store) => store.put(item)); },
  async deletePromptTemplate(id: string) { await query("promptTemplates", "readwrite", (store) => store.delete(id)); },
};
