import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {Type} from 'typebox';
import {
  createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager,
  SettingsManager, createReadToolDefinition,
} from '@earendil-works/pi-coding-agent';

const execFileAsync = promisify(execFile);
export const MODEL = process.env.PB_MODEL_ID || 'Qwen3.8-27B';
export const ENDPOINT = process.env.PB_MODEL_BASE_URL || 'http://127.0.0.1:8099';
export const sha = value => crypto.createHash('sha256').update(value).digest('hex');
export const writeJSON = (file, value) => {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n');
  fs.renameSync(temporary, file);
};
const images = new Set(['.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff','.avif','.heic']);

export function makeReadTool(cwd, python, parser, auditPath) {
  const native = createReadToolDefinition(cwd);
  return {
    ...native,
    description: native.description + ' Original PDF sources support text or visual reads. Set render=true with pages to view a source page; crop optionally selects x0,y0,x1,y1 in PDF points. Set extract_images=true to extract original embedded images. Source image files under materials/ are readable. Generated presentation previews remain unavailable.',
    parameters: Type.Object({...native.parameters.properties,
      pages: Type.Optional(Type.String({description:'Source PDF page selection, e.g. 1-3,5. Visual reads support up to 3 pages per call.'})),
      render: Type.Optional(Type.Boolean({description:'Return original PDF page images for visual understanding.'})),
      crop: Type.Optional(Type.String({description:'PDF region x0,y0,x1,y1 in page points; requires render=true.'})),
      extract_images: Type.Optional(Type.Boolean({description:'Extract original embedded PDF image objects and view up to 4 results.'})),
    }),
    async execute(id, args, signal, onUpdate, ctx) {
      let source = path.resolve(cwd, args.path.replace(/^@/, '').replace(/^~(?=\/)/, process.env.HOME));
      try { source = await fsp.realpath(source); } catch { /* Preserve native missing-file errors. */ }
      const inMaterials = source.startsWith(path.join(cwd, 'materials') + path.sep);
      const ext = path.extname(source).toLowerCase();
      if (images.has(ext) && !inMaterials) {
        fs.appendFileSync(auditPath, JSON.stringify({at_utc:new Date().toISOString(), event:'generated_image_read_blocked', source, rasterized:false}) + '\n');
        throw new Error('Generated presentation image/preview reading is disabled. Read code/text and finish after successful export. Source images under materials/ remain readable.');
      }
      let head = Buffer.alloc(0);
      try {
        const fd = await fsp.open(source, 'r');
        try { head = Buffer.alloc(1024); const r = await fd.read(head,0,1024,0); head = head.subarray(0,r.bytesRead); }
        finally { await fd.close(); }
      } catch { return native.execute(id, args, signal, onUpdate, ctx); }
      if (ext !== '.pdf' && !head.includes(Buffer.from('%PDF-'))) {
        const result=await native.execute(id, args, signal, onUpdate, ctx);
        if(images.has(ext)&&inMaterials)fs.appendFileSync(auditPath,JSON.stringify({at_utc:new Date().toISOString(),event:'source_image_read',source,source_sha256:sha(await fsp.readFile(source)),returned_image_blocks:(result.content||[]).filter(c=>c.type==='image').length})+'\n');
        return result;
      }
      if(args.render || args.extract_images){
        if(!inMaterials)throw new Error('Visual PDF reads are for original source files under materials/.');
        const destination=path.join(cwd,'materials','pdf_views');
        const helper=path.join(path.dirname(parser),'pdf_tools.py');
        const command=[helper,source,'--pages',args.pages||'1','--out-dir',destination,'--source-root',path.join(cwd,'materials'),'--max-pages','3'];
        if(args.crop)command.push('--crop',args.crop);
        if(args.extract_images)command.push('--extract-images');
        const output=await execFileAsync(python,command,{signal,maxBuffer:16*1024*1024});
        const report=JSON.parse(output.stdout);
        const content=[{type:'text',text:'Original PDF source images. Crop coordinates are in PDF points. Reuse these source assets with attribution.\n'+JSON.stringify(report)}];
        for(const asset of report.images.slice(0,4)){
          const result=await native.execute(id,{path:asset.path},signal,onUpdate,ctx);
          content.push(...(result.content||[]));
        }
        fs.appendFileSync(auditPath,JSON.stringify({at_utc:new Date().toISOString(),event:args.extract_images?'source_pdf_images_extracted':'source_pdf_visual_read',source,source_sha256:report.source_pdf_sha256,pages:args.pages||'1',crop:args.crop||null,images:report.images,returned_image_blocks:content.filter(c=>c.type==='image').length})+'\n');
        return {content,details:{source_pdf_visuals:report}};
      }
      const sourceHash = sha(await fsp.readFile(source));
      const cache = path.join(cwd,'.pdf-read-text-cache');
      await fsp.mkdir(cache, {recursive:true,mode:0o700});
      const target = path.join(cache,`${sourceHash}-${sha(args.pages || '').slice(0,12)}.txt`);
      if (!fs.existsSync(target)) {
        const command = [parser,source];
        if (args.pages) command.push('--pages',args.pages);
        try {
          const result = await execFileAsync(python, command, {signal,maxBuffer:128*1024*1024});
          await fsp.writeFile(target, result.stdout);
          if (result.stderr) fs.appendFileSync(auditPath,JSON.stringify({at_utc:new Date().toISOString(),event:'parser_diagnostic',source,stderr:result.stderr})+'\n');
        } catch (error) { throw new Error(`PDF text parsing failed; try render=true for this original source PDF: ${error.stderr || error.message}`); }
      }
      fs.appendFileSync(auditPath,JSON.stringify({at_utc:new Date().toISOString(),event:'pdf_text_read',source,source_sha256:sourceHash,pages:args.pages||null,text_file:target,text_sha256:sha(await fsp.readFile(target)),rasterized:false,ocr_used:false})+'\n');
      return native.execute(id,{path:target,offset:args.offset,limit:args.limit},signal,onUpdate,ctx);
    },
  };
}

export async function createHarness({cwd,logDir,python,parser,maxTurns=80,systemPromptOverride=null,sampling={}}) {
  const agentDir = path.join(logDir,'agent-config');
  fs.mkdirSync(agentDir,{recursive:true});
  const modelConfig = {providers:{'presentbench-local':{
    baseUrl:ENDPOINT,api:'anthropic-messages',apiKey:'local-vllm-no-secret',
    models:[{id:MODEL,name:MODEL,reasoning:true,input:['text','image'],
      contextWindow:131072,maxTokens:32768,
      cost:{input:0,output:0,cacheRead:0,cacheWrite:0},
      compat:{allowEmptySignature:true,supportsEagerToolInputStreaming:false,supportsStrictTools:false},
    }],
  }}};
  writeJSON(path.join(agentDir,'models.json'),modelConfig);
  const modelRuntime = await ModelRuntime.create({
    authPath:path.join(agentDir,'auth.json'),modelsPath:path.join(agentDir,'models.json'),
    modelsStorePath:path.join(agentDir,'catalog.json'),allowModelNetwork:false,
  });
  const model = modelRuntime.getModel('presentbench-local',MODEL);
  if (!model) throw new Error('Local model not found; refusing any fallback');
  const settings = {
    compaction:{enabled:true,reserveTokens:32768,keepRecentTokens:20000},
    retry:{enabled:true,maxRetries:1,provider:{maxRetries:0}},
    httpIdleTimeoutMs:0,cacheWarming:'off',thinkingBudgets:{medium:sampling.thinking_budget??8192},
  };
  const settingsManager=SettingsManager.inMemory(settings);
  const policy = 'Read instructions.md and all sources listed in materials.json, including supplied source images. Complete the user query using local tools. Create the final genuine PowerPoint at outputs/presentation.pptx; python-pptx is installed. Ground content in the supplied materials, preserve figure captions and credits, and use relevant original figures in the deck. Original source PDFs may be viewed as images, rendered, cropped, and have embedded images extracted: use read with render=true, pages and optional crop=x0,y0,x1,y1 in PDF points, or run python pdf_tools.py --help. Keep source-derived images under materials/ so they are readable. Preserve source scripts and verify the PPTX opens with at least one slide. Native read/write/edit/bash/grep/find remain available. Generated presentation preview-image feedback is disabled; source-material visual inspection is allowed. Keep temporary files in scratch/ and do not read other cases or change model services. Never launch other agents. Files marked style_reference_only are visual design references, never factual content sources.';
  const resourceLoader=new DefaultResourceLoader({cwd,agentDir,settingsManager,
    noExtensions:true,noSkills:true,noPromptTemplates:true,noThemes:true,noContextFiles:true,
    appendSystemPromptOverride:()=>[systemPromptOverride || policy],
  });
  await resourceLoader.reload();
  const read = makeReadTool(cwd,python,parser,path.join(logDir,'read-audit.jsonl'));
  const {session,modelFallbackMessage}=await createAgentSession({cwd,agentDir,modelRuntime,
    model,thinkingLevel:'medium',settingsManager,resourceLoader,
    sessionManager:SessionManager.create(cwd,path.join(logDir,'sessions')),
    tools:['read','bash','edit','write','grep','find'],customTools:[read],
  });
  if (modelFallbackMessage || session.model.id !== MODEL || session.thinkingLevel !== 'medium') {
    session.dispose(); throw new Error('Unexpected model fallback or thinking mode');
  }
  let completedTurns=0,limitReached=false,providerRequests=0;
  const originalFinish=session.agent.finishTurn;
  session.agent.finishTurn=async (turn,signal)=>{
    const decision=await originalFinish?.(turn,signal);
    completedTurns++;
    if (completedTurns>=maxTurns) {
      limitReached=turn.message.content.some(block=>block.type==='toolCall');
      return {action:'end'};
    }
    return decision;
  };
  const originalPayload=session.agent.onPayload;
  session.agent.onPayload=async (payload,model)=>{
    const body=await originalPayload?.(payload,model) || payload;
    body.max_tokens=sampling.max_tokens??32768;
    body.thinking={type:'enabled',budget_tokens:sampling.thinking_budget??8192};
    body.output_config={...(body.output_config||{}),effort:'medium'};
    // Match the prior CC request, which left temperature to the same vLLM backend.
    if(Number.isFinite(sampling.temperature))body.temperature=sampling.temperature;else delete body.temperature;
    if(Number.isFinite(sampling.top_p))body.top_p=sampling.top_p;
    if(Number.isInteger(sampling.top_k))body.top_k=sampling.top_k;
    if(Number.isInteger(sampling.base_seed)&&sampling.base_seed>0)body.seed=sampling.base_seed+providerRequests;else delete body.seed;
    providerRequests++;
    fs.appendFileSync(path.join(logDir,'request-audit.jsonl'),JSON.stringify({
      at_utc:new Date().toISOString(),request_index:providerRequests,model:body.model,
      max_tokens:body.max_tokens,thinking:body.thinking,output_config:body.output_config,
      temperature:body.temperature??null,temperature_explicit:Number.isFinite(body.temperature),top_p:body.top_p??null,top_k:body.top_k??null,seed:body.seed??null,
      tools:(body.tools||[]).map(t=>t.name),message_count:body.messages?.length,
      payload_sha256:sha(JSON.stringify(body)),
      // Full local request is retained for reproducibility; it contains only public inputs and local generation.
      request_file:`requests/${String(providerRequests).padStart(3,'0')}.json.gz`,
    })+'\n');
    const requestPath=path.join(logDir,'requests',`${String(providerRequests).padStart(3,'0')}.json.gz`);
    fs.mkdirSync(path.dirname(requestPath),{recursive:true});
    fs.writeFileSync(requestPath,gzipSync(Buffer.from(JSON.stringify(body,null,2)+'\n'),{level:6}));
    return body;
  };
  fs.writeFileSync(path.join(logDir,'system-prompt.txt'),session.systemPrompt);
  writeJSON(path.join(logDir,'native-session-settings.json'),{model:session.model,
    thinking_level:session.thinkingLevel,active_tools:session.getActiveToolNames(),settings,
    max_turns:maxTurns,turn_definition:'One completed Pi assistant response plus its tool batch; includes final response. Compaction requests recorded separately.',
    timeout_seconds:null,wall_clock_limit_enabled:false,pdf_read_mode:'text_and_source_visuals',pdf_source_rendering:true,pdf_source_image_extraction:true,pdf_source_region_crop:true,
    source_images_allowed:true,generated_image_read_policy:'Only source images under materials/ are readable; other image reads blocked.',
    pi_package:'@earendil-works/pi-coding-agent',pi_version:'1.4.0',isolated_probe:!!systemPromptOverride,
  });
  return {session,read,stats:()=>({completed_turns:completedTurns,turn_limit_reached:limitReached,provider_requests:providerRequests})};
}
