// ===================== T65: ESPELHO SEC NO GITHUB (PROPOSTA — NAO APLICAR AINDA) =====================
// Colar como ULTIMO arquivo (depois de SPS_SecMirrorDriveT61). Redefine spsSECRelayWireV032_ por ATRIBUICAO.
// Le o release publico SPStocks/sps-sec-mirror (mesmo schema/SHA de T61). Se o GitHub falhar de forma
// recuperavel, cai no espelho do Drive (T61). Nao chama a SEC. Nao seta SPS_SEC_VALIDATED_V028.
// Sem retry em loop. Rollback por Script Property SPS_SEC_MIRROR_ROLLBACK_T65:
//   'DRIVE' = so o espelho T61; 'RELAY' = relay V032 original (tambem honra SPS_SEC_MIRROR_ROLLBACK_T61=RELAY).
var SPS_SEC_MIRROR_T65=Object.freeze({
 repo:'SPStocks/sps-sec-mirror',tag:'latest',stableManifest:'SPS_SEC_MIRROR_MANIFEST_T61.json',
 schema:'SPS_SEC_MIRROR_MANIFEST_T61_V1',staleMs:16*3600000,missRetryMs:8*3600000,
 transport:'MANAGED_SHARED_ORIGIN_V032',mirror:'SEC_MIRROR_GITHUB_RELEASE_T65',
 rollbackKey:'SPS_SEC_MIRROR_ROLLBACK_T65',lastReceiptKey:'SPS_SEC_MIRROR_LAST_RECEIPT_T65',
 repoProp:'SPS_SEC_MIRROR_GITHUB_REPO_T65',tagProp:'SPS_SEC_MIRROR_GITHUB_TAG_T65'
});
var SPS_SEC_RELAY_WIRE_ORIG_T65=(typeof SPS_SEC_RELAY_WIRE_ORIG_T65==='function')?SPS_SEC_RELAY_WIRE_ORIG_T65
 :(typeof SPS_SEC_RELAY_WIRE_ORIG_T61==='function')?SPS_SEC_RELAY_WIRE_ORIG_T61
 :(typeof spsSECRelayWireV032_==='function'?spsSECRelayWireV032_:null);
var SPS_SEC_MIRROR_WIRE_DRIVE_T65=(typeof spsSecMirrorWireT61_==='function')?spsSecMirrorWireT61_:null;
var SPS_SEC_MIRROR_MEMO_T65={manifest:null,manifestName:null};
function spsSecMirrorGithubRepoT65_(){
 const p=PropertiesService.getScriptProperties(),r=p.getProperty(SPS_SEC_MIRROR_T65.repoProp)||SPS_SEC_MIRROR_T65.repo;
 if(!/^[\w.-]{1,40}\/[\w.-]{1,100}$/.test(r))throw Error('SEC_MIRROR_GITHUB_REPO_INVALID');return r;
}
function spsSecMirrorGithubTagT65_(){
 const t=PropertiesService.getScriptProperties().getProperty(SPS_SEC_MIRROR_T65.tagProp)||SPS_SEC_MIRROR_T65.tag;
 if(!/^(latest|[\w.-]{1,80})$/.test(t))throw Error('SEC_MIRROR_GITHUB_TAG_INVALID');return t;
}
function spsSecMirrorGithubFileUrlT65_(name){
 if(!/^[\w.-]{1,180}$/.test(name))throw Error('SEC_MIRROR_GITHUB_NAME_INVALID');
 const repo=spsSecMirrorGithubRepoT65_(),tag=spsSecMirrorGithubTagT65_();
 return tag==='latest'
  ?('https://github.com/'+repo+'/releases/latest/download/'+name)
  :('https://github.com/'+repo+'/releases/download/'+tag+'/'+name);
}
function spsSecMirrorModeT65_(){
 const p=PropertiesService.getScriptProperties();
 if(p.getProperty(SPS_SEC_MIRROR_T65.rollbackKey)==='RELAY'||p.getProperty('SPS_SEC_MIRROR_ROLLBACK_T61')==='RELAY')return 'RELAY_ROLLBACK';
 if(p.getProperty(SPS_SEC_MIRROR_T65.rollbackKey)==='DRIVE')return 'DRIVE_FALLBACK';
 return 'GITHUB';
}
function spsSecMirrorFailT65_(status,reason,ms,extra){const e=Error('SEC_MIRROR_'+reason);e.spsSECFailure=Object.assign({status,reason},ms?{until:Date.now()+ms}:{},extra||{});return e;}
function spsSecMirrorGithubFetchT65_(name,range){
 const url=spsSecMirrorGithubFileUrlT65_(name),opt={muteHttpExceptions:true,followRedirects:true};
 if(range)opt.headers={Range:'bytes='+range.o+'-'+(range.o+range.n-1)};
 const r=UrlFetchApp.fetch(url,opt),code=r.getResponseCode();
 if(code!==200&&code!==206)throw spsSecMirrorFailT65_('WAIT_SOURCE_RECOVERY','GITHUB_HTTP_'+code,1800000,{file:name});
 return {code:code,bytes:r.getContent(),blob:r.getBlob()};
}
function spsSecMirrorGithubManifestT65_(){
 const memo=SPS_SEC_MIRROR_MEMO_T65;if(memo.manifest)return memo.manifest;
 const got=spsSecMirrorGithubFetchT65_(SPS_SEC_MIRROR_T65.stableManifest,null);
 const text=Utilities.newBlob(got.bytes).getDataAsString('UTF-8');
 const m=JSON.parse(text);
 if(!m||m.schema!==SPS_SEC_MIRROR_T65.schema||!m.items||typeof m.items!=='object'||!Number.isFinite(Date.parse(m.generatedUTC)))throw Error('SEC_MIRROR_MANIFEST_INVALID');
 memo.manifest=m;memo.manifestName=SPS_SEC_MIRROR_T65.stableManifest+'@'+spsSecMirrorGithubTagT65_();return m;
}
/** Le um membro gzip do pack (Range; se o host devolver o arquivo inteiro em HTTP 200, recorta). Confere sha256/tamanho/UTF-8. */
function spsSecMirrorGithubReadItemT65_(path,x){
 if(!x||!x.p||!Number.isSafeInteger(x.o)||!Number.isSafeInteger(x.n)||x.o<0||x.n<18||x.n>60*1024*1024)throw Error('SEC_MIRROR_ITEM_RANGE_INVALID');
 const got=spsSecMirrorGithubFetchT65_(x.p,{o:x.o,n:x.n});
 let gz=got.bytes;
 if(got.code===200){if(gz.length<x.o+x.n)throw Error('SEC_MIRROR_RANGE_SHORT');if(gz.length!==x.n)gz=gz.slice(x.o,x.o+x.n);}
 if(gz.length!==x.n)throw Error('SEC_MIRROR_RANGE_LENGTH');
 const raw=Utilities.ungzip(Utilities.newBlob(gz,'application/x-gzip')),bytes=raw.getBytes();
 const sha=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,bytes).map(b=>('0'+(b&255).toString(16)).slice(-2)).join('');
 if(sha!==x.s||bytes.length!==x.b)throw Error('SEC_MIRROR_HASH_MISMATCH');
 const text=raw.getDataAsString('UTF-8');if(spsDigest_(text)!==x.s)throw Error('SEC_MIRROR_UTF8_ROUNDTRIP_MISMATCH');
 return {bytes:bytes,text:text,sha:sha};
}
function spsSecMirrorGithubCanFallbackT65_(e){
 const msg=String(e&&e.message||e),reason=e&&e.spsSECFailure&&e.spsSECFailure.reason||'';
 if(/HASH_MISMATCH|UTF8_UNVERIFIED|UTF8_ROUNDTRIP|ITEM_RANGE_INVALID|MANIFEST_INVALID|NOT_ALLOWED|NOT_VERIFIED|BLOCKED_SEC_ORIGIN/.test(msg+' '+reason))return false;
 return typeof SPS_SEC_MIRROR_WIRE_DRIVE_T65==='function';
}
/** Espelho com o MESMO contrato de spsSECRelayWireV032_(url,probe). */
function spsSecMirrorGithubWireT65_(url,probe){
 spsSECRelayAllowedV032_(url);spsV028BeforeHTTP_();
 const p=PropertiesService.getScriptProperties(),cfg=spsSECRelayConfigV032_(p),stop=p.getProperty('SPS_SEC_RELAY_STOP_V032');
 if(stop){const e=Error('SEC_DEDICATED_ORIGIN_STOP_REQUIRES_REVIEW');e.spsSECFailure={status:'BLOCKED_SEC_ORIGIN',reason:'MANAGED_SEC_ORIGIN_STOP_REQUIRES_REVIEW',blockOrigin:true};throw e;}
 if(!probe&&(p.getProperty('SPS_SEC_RELAY_ENABLED_V032')!=='VERIFIED'||p.getProperty('SPS_SEC_RELAY_VERIFIED_ORIGIN_V032')!==cfg.origin))throw Error('SEC_DEDICATED_ORIGIN_NOT_VERIFIED');
 const m=spsSecMirrorGithubManifestT65_(),path=String(url).replace(/^https:\/\/www\.sec\.gov\//,''),x=m.items[path],age=Date.now()-Date.parse(m.generatedUTC);
 if(!x){
  if(age>SPS_SEC_MIRROR_T65.staleMs)throw spsSecMirrorFailT65_('WAIT_SOURCE_RECOVERY','STALE_MANIFEST',3600000,{manifest:SPS_SEC_MIRROR_MEMO_T65.manifestName});
  if(typeof spsSecMirrorRequestT61_==='function')try{spsSecMirrorRequestT61_(url);}catch(e){}
  if(path.indexOf('/daily-index/')>0)throw spsSecMirrorFailT65_('WAIT_SOURCE_RECOVERY','INDEX_NOT_YET_MIRRORED',3600000);
  throw spsSecMirrorFailT65_('SOURCE_RETRY','SEC_MIRROR_MISS_REQUESTED',SPS_SEC_MIRROR_T65.missRetryMs);
 }
 if(x.h!==200||!x.p){
  const reason=x.x||('SEC_HTTP_'+x.h);
  p.setProperty('SPS_SEC_RELAY_LAST_ERROR_V032',JSON.stringify({sourceUrl:url,httpStatus:502,error:'SEC_MIRROR_'+reason,upstreamStatus:x.h||null,retryAfterSeconds:null,failureClass:'SEC_MIRROR_T65',failureReason:['WIRE_BODY_TOO_LARGE','DECODED_BODY_TOO_LARGE','ENCODING_TRUNCATED_OR_TRAILING'].includes(reason)?reason:null,declaredWireBytes:null,observedWireBytes:null,observedUTC:new Date().toISOString()}));
  if(['WIRE_BODY_TOO_LARGE','DECODED_BODY_TOO_LARGE'].includes(reason)){p.setProperty('SPS_SEC_RELAY_CAPACITY_CP255_'+spsDigest_(url).slice(0,20),p.getProperty('SPS_SEC_RELAY_LAST_ERROR_V032'));throw spsSecMirrorFailT65_('WAIT_SOURCE_RECOVERY','SEC_FETCH_FAILED_COOLDOWN',600000);}
  if(reason==='UTF8_UNVERIFIED')throw Error('SEC_MIRROR_UTF8_UNVERIFIED');
  throw spsSecMirrorFailT65_('WAIT_SOURCE_RECOVERY','SEC_NON200_NO_REDIRECT',600000);
 }
 const got=spsSecMirrorGithubReadItemT65_(path,x);
 const receipt={transport:SPS_SEC_MIRROR_T65.transport,mirror:SPS_SEC_MIRROR_T65.mirror,originId:m.origin,sourceUrl:url,collectedUTC:x.c,sha256:got.sha,userAgent:m.userAgent||'DECLARED',
  representation:'HTTP_DECOMPRESSED_BYTES_UTF8',httpStatus:200,mirrorPack:x.p,mirrorManifest:SPS_SEC_MIRROR_MEMO_T65.manifestName};
 p.setProperty('SPS_SEC_RELAY_LAST_RECEIPT_V032',JSON.stringify(receipt));p.setProperty(SPS_SEC_MIRROR_T65.lastReceiptKey,JSON.stringify(receipt));
 return {getResponseCode:function(){return 200;},getContentText:function(){return got.text;},getAllHeaders:function(){return {'X-SPS-Origin':m.origin};},spsTransport:receipt};
}
function spsSecMirrorGithubOrDriveT65_(url,probe){
 try{return spsSecMirrorGithubWireT65_(url,probe);}
 catch(e){
  if(!spsSecMirrorGithubCanFallbackT65_(e))throw e;
  Logger.log('SPS_SEC_MIRROR_T65_FALLBACK_DRIVE '+String(e&&e.message||e).slice(0,120));
  return SPS_SEC_MIRROR_WIRE_DRIVE_T65(url,probe);
 }
}
spsSECRelayWireV032_=function(url,probe){
 const mode=spsSecMirrorModeT65_();
 if(mode==='RELAY_ROLLBACK'){if(typeof SPS_SEC_RELAY_WIRE_ORIG_T65!=='function')throw Error('SEC_MIRROR_ROLLBACK_ORIGINAL_MISSING');return SPS_SEC_RELAY_WIRE_ORIG_T65(url,probe);}
 if(mode==='DRIVE_FALLBACK'){if(typeof SPS_SEC_MIRROR_WIRE_DRIVE_T65!=='function')throw Error('SEC_MIRROR_DRIVE_T61_MISSING');return SPS_SEC_MIRROR_WIRE_DRIVE_T65(url,probe);}
 return spsSecMirrorGithubOrDriveT65_(url,probe);
};
/** Runner de verificacao (somente leitura do espelho GitHub: 0 chamadas SEC, 0 escritas de validacao). */
function spsSecMirrorVerifyT65(){
 const out={status:'SEC_MIRROR_VERIFY_T65',mode:spsSecMirrorModeT65_(),sourceCalls:0,writes:0,checks:{},repo:spsSecMirrorGithubRepoT65_(),tag:spsSecMirrorGithubTagT65_()};
 try{
  const m=spsSecMirrorGithubManifestT65_(),keys=Object.keys(m.items),idx=keys.filter(function(k){return k.indexOf('/daily-index/')>0&&m.items[k].h===200&&m.items[k].p;}).sort();
  out.manifest={name:SPS_SEC_MIRROR_MEMO_T65.manifestName,generatedUTC:m.generatedUTC,origin:m.origin,items:keys.length,
   bodiesOk:keys.filter(function(k){return k.indexOf('/data/')>0&&m.items[k].h===200&&m.items[k].p;}).length,indexesOk:idx.length,packs:(m.packs||[]).length};
  out.checks.manifestFresh=(Date.now()-Date.parse(m.generatedUTC))<=SPS_SEC_MIRROR_T65.staleMs;
  if(idx.length){const k=idx[idx.length-1],got=spsSecMirrorGithubReadItemT65_(k,m.items[k]);out.indexTest={path:k,shaOk:got.sha===m.items[k].s,bytes:got.bytes.length};out.checks.indexSha=out.indexTest.shaOk;}
 }catch(e){out.error=String(e&&e.message||e).slice(0,200);out.failure=e&&e.spsSECFailure||null;}
 out.nextStep=out.mode!=='GITHUB'?'ROLLBACK_ATIVO':out.checks.indexSha?'GITHUB_OK':'ESPELHO_SEM_INDICE_OU_ERRO';
 Logger.log('SPS_SEC_MIRROR_VERIFY_T65 '+JSON.stringify(out));return out;
}
