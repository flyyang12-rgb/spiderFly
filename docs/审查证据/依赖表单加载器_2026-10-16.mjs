// Copy alongside the check script into a fresh isolated frontend, never run here.
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
const front=dirname(fileURLToPath(import.meta.url)).replaceAll('\\','/')
if(!/\/spiderfly-check-[^/]+\/frontend$/.test(front))throw new Error('Requires an isolated frontend directory')
const require=createRequire(pathToFileURL(join(front,'node_modules/package.json')))
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc')
export async function resolve(specifier,context,nextResolve){
 if(specifier==='vue')return {url:pathToFileURL(require.resolve('vue')).href,shortCircuit:true}
 if(specifier.startsWith('.')&&context.parentURL){const url=new URL(specifier+'.js',context.parentURL);if(existsSync(fileURLToPath(url)))return {url:url.href,shortCircuit:true}}
 return nextResolve(specifier,context)
}
export async function load(url,context,nextLoad){
 if(!url.endsWith('.vue'))return nextLoad(url,context)
 const filename=fileURLToPath(url)
 if(!filename.replaceAll('\\','/').startsWith(front+'/src/'))throw new Error('Only isolated Vue sources are allowed')
 const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
 const script=compileScript(descriptor,{id:url,genDefaultAs:'__sfc__'})
 const template=compileTemplate({source:descriptor.template.content,filename,id:url,compilerOptions:{bindingMetadata:script.bindings}})
 if(template.errors.length)throw new Error(JSON.stringify(template.errors))
 return {format:'module',shortCircuit:true,source:script.content+'\n'+template.code+'\n__sfc__.render=render;export default __sfc__;'}
}
