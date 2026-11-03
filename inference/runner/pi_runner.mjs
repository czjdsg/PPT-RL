import fs from 'node:fs';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {fileURLToPath} from 'node:url';
import {createHarness,writeJSON} from './harness.mjs';

const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const {cwd,log_dir:logDir,python,max_turns:maxTurns}=config;
const here=path.dirname(fileURLToPath(import.meta.url));
const start=performance.now();
const controllerPid=process.ppid;
// Stop orphan computation if the owning lease client exits unexpectedly.
setInterval(()=>{if(process.ppid!==controllerPid)process.kill(process.pid,'SIGTERM');},1000).unref();
const harness=await createHarness({cwd,logDir,python,parser:path.join(here,'read_text.py'),maxTurns,sampling:config.sampling||{},toolPolicy:config.tool_policy||{}});
const {session}=harness;
const events=fs.openSync(path.join(logDir,'events.jsonl'),'wx');
let responses=0,tools=0,imageBlocks=0,thinkingBlocks=0,thinkingChars=0,finalMessage=null;
let firstToolAt=null,lastProgress=0,cancelled=false;
const record=event=>fs.writeSync(events,JSON.stringify({at_utc:new Date().toISOString(),elapsed_seconds:(performance.now()-start)/1000,...event})+'\n');
const progress=()=>writeJSON(path.join(logDir,'live.json'),{
  status:cancelled?'cancelled_by_user':'running',at_utc:new Date().toISOString(),
  elapsed_seconds:(performance.now()-start)/1000,assistant_responses:responses,tool_calls:tools,
  image_blocks:imageBlocks,thinking_blocks:thinkingBlocks,thinking_characters:thinkingChars,
  ...harness.stats(),first_tool_at_utc:firstToolAt,
});
const unsubscribe=session.subscribe(event=>{
  if (event.type==='message_update') {
    // Store raw deltas; omit the cumulative partial message that would duplicate the transcript quadratically.
    const {partial,...delta}=event.assistantMessageEvent;
    record({type:event.type,assistantMessageEvent:delta});
  } else if (event.type==='agent_end') {
    record({type:event.type,message_count:event.messages?.length});
  } else if (event.type==='turn_end') {
    record({type:event.type,message_stop_reason:event.message?.stopReason,tool_result_count:event.toolResults?.length});
  } else record(event);
  if(event.type==='message_end' && event.message.role==='assistant') {
    responses++;finalMessage=event.message;
    const thought=event.message.content.filter(x=>x.type==='thinking');
    thinkingBlocks+=thought.length;thinkingChars+=thought.reduce((n,x)=>n+(x.thinking?.length||0),0);
  }
  if(event.type==='tool_execution_start') {tools++;firstToolAt ||= new Date().toISOString();}
  if(event.type==='tool_execution_end') imageBlocks+=(event.result?.content||[]).filter(x=>x.type==='image').length;
  if(performance.now()-lastProgress>3000 || ['message_end','tool_execution_end','agent_settled'].includes(event.type)) {progress();lastProgress=performance.now();}
});
for(const signal of ['SIGTERM','SIGINT']) process.once(signal,()=>{
  cancelled=true;record({type:'controller_cancel',signal});void session.abort();
});
let caught=null;
try {await session.prompt(config.query || fs.readFileSync(path.join(logDir,'prompt.txt'),'utf8'));}
catch(error){caught=String(error?.stack||error);}
finally {
  const stats=harness.stats();
  const status=cancelled?'cancelled_by_user':stats.turn_limit_reached?'turn_limit':caught || ['error','aborted','length'].includes(finalMessage?.stopReason) || !finalMessage?'failed':'completed';
  const report={status,...stats,assistant_responses:responses,tool_calls:tools,
    returned_image_blocks:imageBlocks,thinking_blocks:thinkingBlocks,thinking_characters:thinkingChars,
    elapsed_seconds:(performance.now()-start)/1000,first_tool_at_utc:firstToolAt,
    final:finalMessage,error:caught,session_file:session.sessionFile,
    event_log_note:'All finalized messages, tool events and raw streaming deltas retained. Redundant cumulative partial messages omitted.',
  };
  writeJSON(path.join(logDir,'runner-result.json'),report);
  writeJSON(path.join(logDir,'live.json'),{...report,at_utc:new Date().toISOString()});
  unsubscribe();session.dispose();fs.closeSync(events);
  console.log(JSON.stringify({status,assistant_responses:responses,tool_calls:tools,...stats}));
  process.exitCode=status==='completed'?0:1;
}
