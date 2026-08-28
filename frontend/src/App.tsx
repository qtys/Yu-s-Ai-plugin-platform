import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import './App.css'
import './Desktop.css'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api'
type Character = { id:number; name:string; description:string; system_prompt:string }
type Conversation = { id:number; character_id:number; title:string }
type Message = { role:'user'|'assistant'; content:string }
type Settings = { base_url:string; api_key:string; model:string; temperature:number; max_tokens:number }

async function request<T>(path:string, options?:RequestInit):Promise<T>{
  const response=await fetch(`${API}${path}`,{headers:{'Content-Type':'application/json'},...options})
  if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail??`请求失败 (${response.status})`)}
  return response.json()
}

export default function App(){
  const [characters,setCharacters]=useState<Character[]>([]),[conversations,setConversations]=useState<Conversation[]>([]),[messages,setMessages]=useState<Message[]>([])
  const [activeCharacter,setActiveCharacter]=useState<number|null>(null),[activeConversation,setActiveConversation]=useState<number|null>(null)
  const [input,setInput]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const [pinned,setPinned]=useState(false),[mini,setMini]=useState(false)
  const [backendReady,setBackendReady]=useState(false)
  const [panel,setPanel]=useState<'chat'|'characters'|'settings'>('chat')
  const [settings,setSettings]=useState<Settings>({base_url:'https://api.openai.com/v1',api_key:'',model:'gpt-4o-mini',temperature:.8,max_tokens:2048})
  const [draft,setDraft]=useState({name:'',description:'',system_prompt:''})
  const abortRef=useRef<AbortController|null>(null)
  const desktop='__TAURI_INTERNALS__' in window

  useEffect(()=>{let cancelled=false;(async()=>{
    for(let attempt=0;attempt<30;attempt++){
      try{await request('/health');if(cancelled)return;setBackendReady(true);await Promise.all([
        request<Character[]>('/characters').then(data=>{setCharacters(data);if(data[0])setActiveCharacter(data[0].id)}),
        request<Settings>('/settings').then(setSettings),
      ]);return}catch{await new Promise(resolve=>setTimeout(resolve,500))}
    }
    if(!cancelled)setError('本地后端启动失败，请查看安装目录 logs/sidecar.log')
  })();return()=>{cancelled=true}},[])
  useEffect(()=>{if(activeCharacter)request<Conversation[]>(`/conversations?character_id=${activeCharacter}`).then(setConversations).catch(e=>setError(e.message))},[activeCharacter])
  useEffect(()=>{if(activeConversation)request<Message[]>(`/conversations/${activeConversation}/messages`).then(setMessages).catch(e=>setError(e.message))},[activeConversation])

  async function createCharacter(event:FormEvent){event.preventDefault();if(!backendReady){setError('本地后端仍在启动，请稍候');return}try{const value=await request<Character>('/characters',{method:'POST',body:JSON.stringify(draft)});setCharacters(c=>[value,...c]);setActiveCharacter(value.id);setDraft({name:'',description:'',system_prompt:''});setPanel('chat')}catch(e){setError((e as Error).message)}}
  async function createConversation(){if(!activeCharacter){setPanel('characters');return}try{const value=await request<Conversation>('/conversations',{method:'POST',body:JSON.stringify({character_id:activeCharacter})});setConversations(c=>[value,...c]);setActiveConversation(value.id);setPanel('chat')}catch(e){setError((e as Error).message)}}
  async function saveSettings(event:FormEvent){event.preventDefault();try{await request('/settings',{method:'PUT',body:JSON.stringify(settings)});setPanel('chat');setError('')}catch(e){setError((e as Error).message)}}
  async function togglePin(){try{const value=!pinned;await invoke('set_always_on_top',{enabled:value});setPinned(value)}catch(e){setError(String(e))}}
  async function toggleMini(){try{const value=!mini;await invoke('set_mini_mode',{enabled:value});setMini(value);if(value)setPinned(true)}catch(e){setError(String(e))}}
  async function send(event:FormEvent){
    event.preventDefault();const content=input.trim();if(!content||busy)return;let id=activeConversation
    try{
      if(!id){if(!activeCharacter)throw new Error('请先创建一个角色');const value=await request<Conversation>('/conversations',{method:'POST',body:JSON.stringify({character_id:activeCharacter})});id=value.id;setActiveConversation(id);setConversations(c=>[value,...c])}
      setInput('');setBusy(true);setError('');setMessages(c=>[...c,{role:'user',content},{role:'assistant',content:''}])
      const controller=new AbortController();abortRef.current=controller
      const response=await fetch(`${API}/conversations/${id}/chat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content}),signal:controller.signal})
      if(!response.ok){const data=await response.json();throw new Error(data.detail??'发送失败')}if(!response.body)throw new Error('浏览器不支持流式响应')
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
      while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const lines=buffer.split('\n');buffer=lines.pop()??'';for(const line of lines){if(!line)continue;const data=JSON.parse(line);if(data.error)throw new Error(data.error);if(data.token)setMessages(c=>c.map((m,i)=>i===c.length-1?{...m,content:m.content+data.token}:m))}}
      setConversations(await request(`/conversations?character_id=${activeCharacter}`))
    }catch(e){if((e as Error).name!=='AbortError')setError((e as Error).message)}finally{setBusy(false);abortRef.current=null}
  }
  const character=characters.find(x=>x.id===activeCharacter),conversation=conversations.find(x=>x.id===activeConversation)

  return <div className={mini?'shell mini':'shell'}><aside className="sidebar">
    <div className="brand"><span className="brand-mark">雨</span><div><strong>Yu's AI</strong><small>私人智能空间</small></div></div>
    <button className="new-chat" onClick={createConversation}>＋ 新建对话</button><div className="sidebar-label">角色</div>
    <div className="character-list">{characters.map(x=><button key={x.id} className={activeCharacter===x.id?'selected character-row':'character-row'} onClick={()=>{setActiveCharacter(x.id);setActiveConversation(null);setMessages([]);setConversations([]);setPanel('chat')}}><span className="avatar">{x.name[0]}</span><span><strong>{x.name}</strong><small>{x.description||'私人角色'}</small></span></button>)}{!characters.length&&<button className="empty-action" onClick={()=>setPanel('characters')}>创建第一个角色</button>}</div>
    <div className="sidebar-label">最近对话</div><div className="conversation-list">{conversations.map(x=><button key={x.id} className={activeConversation===x.id?'selected':''} onClick={()=>{setActiveConversation(x.id);setPanel('chat')}}>{x.title}</button>)}</div>
    <nav><button onClick={()=>setPanel('characters')}>角色管理</button><button onClick={()=>setPanel('settings')}>模型设置</button></nav>
  </aside><main><header><div><strong>{panel==='settings'?'模型设置':panel==='characters'?'创建角色':conversation?.title||character?.name||'开始使用'}</strong><small>{panel==='chat'&&character?`正在与 ${character.name} 对话`:'Yu’s AI Plugin Platform'}</small></div><div className="window-tools">{desktop&&<><button className={pinned?'active':''} onClick={togglePin} title="切换始终置顶">置顶</button><button className={mini?'active':''} onClick={toggleMini} title="切换迷你窗口">{mini?'展开':'迷你'}</button></>}<span className={backendReady?'status':'status starting'}><i/> {backendReady?'本地服务已连接':'正在启动服务'}</span></div></header>
    {error&&<div className="error" onClick={()=>setError('')}>{error}<span>×</span></div>}
    {panel==='settings'&&<section className="form-page"><Heading eyebrow="模型连接" title="连接你的 AI" text="支持 OpenAI Chat Completions 格式的服务。"/><form onSubmit={saveSettings}><Field label="API 地址"><input value={settings.base_url} onChange={e=>setSettings({...settings,base_url:e.target.value})}/></Field><Field label="API Key"><input type="password" value={settings.api_key} onChange={e=>setSettings({...settings,api_key:e.target.value})} placeholder="sk-..."/></Field><Field label="模型名称"><input value={settings.model} onChange={e=>setSettings({...settings,model:e.target.value})}/></Field><div className="form-grid"><Field label="温度"><input type="number" min="0" max="2" step=".1" value={settings.temperature} onChange={e=>setSettings({...settings,temperature:Number(e.target.value)})}/></Field><Field label="最大输出"><input type="number" value={settings.max_tokens} onChange={e=>setSettings({...settings,max_tokens:Number(e.target.value)})}/></Field></div><button className="primary">保存设置</button></form></section>}
    {panel==='characters'&&<section className="form-page"><Heading eyebrow="角色卡" title="创造一个对话角色" text="角色提示词会作为每次对话的行为基础。"/><form onSubmit={createCharacter}><Field label="角色名称"><input required value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} placeholder="例如：雨"/></Field><Field label="一句话介绍"><input value={draft.description} onChange={e=>setDraft({...draft,description:e.target.value})} placeholder="温柔而敏锐的私人助手"/></Field><Field label="系统提示词"><textarea rows={8} value={draft.system_prompt} onChange={e=>setDraft({...draft,system_prompt:e.target.value})} placeholder="描述性格、背景、说话方式和边界……"/></Field><button className="primary">创建角色</button></form></section>}
    {panel==='chat'&&<section className="chat-page"><div className="messages">{!messages.length&&<div className="welcome"><div className="welcome-orb">雨</div><span>你的私人 AI 空间</span><h1>{character?`想和 ${character.name} 聊些什么？`:'先创造一个属于你的角色'}</h1><p>{character?.description||'配置自己的模型、角色与对话，一切数据保留在本地。'}</p>{!character&&<button className="primary" onClick={()=>setPanel('characters')}>创建角色</button>}</div>}{messages.map((m,i)=><article key={i} className={m.role}><div className="message-avatar">{m.role==='user'?'你':character?.name[0]||'AI'}</div><div><strong>{m.role==='user'?'你':character?.name||'助手'}</strong><p>{m.content||<span className="typing">思考中</span>}</p></div></article>)}</div><form className="composer" onSubmit={send}><textarea rows={1} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();e.currentTarget.form?.requestSubmit()}}} placeholder={character?`给 ${character.name} 发消息…`:'请先创建角色'} disabled={!character||busy}/><button type={busy?'button':'submit'} onClick={busy?()=>abortRef.current?.abort():undefined}>{busy?'■':'↑'}</button><small>Enter 发送 · Shift + Enter 换行</small></form></section>}
  </main></div>
}

function Heading({eyebrow,title,text}:{eyebrow:string;title:string;text:string}){return <div className="section-heading"><span>{eyebrow}</span><h1>{title}</h1><p>{text}</p></div>}
function Field({label,children}:{label:string;children:ReactNode}){return <label>{label}{children}</label>}
