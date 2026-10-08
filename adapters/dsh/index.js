/**
 * DeepSeek Harness 宿主插件入口：把本包内的统一 Skill 注册到会话技能目录。
 *
 * 只读取包内文件并调用技能注册服务，不改动 profile 配置，也不安装 Python 依赖；
 * Python 环境、行情数据、账户和报告由使用者在研究工作目录中管理。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SKILL_DIRECTORY = new URL('./skills/multi-asset-investment/', import.meta.url);
const SKILL_FILE = new URL('SKILL.md', SKILL_DIRECTORY);
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** 技能注册服务由基础组合提供，缺失时本行保持等待而不是报错。 */
export const inject = ['skills'];

export function apply(ctx) {
  // register 返回的清理函数交给 Cordis，插件卸载或重载时技能随之撤下。
  ctx.effect(() => ctx.skills.register(skillDefinition()));
}

function skillDefinition() {
  const path = fileURLToPath(SKILL_FILE);
  const raw = readFileSync(path, 'utf8');
  const { fields, body } = splitFrontmatter(raw, path);
  const name = fields.name;
  const description = fields.description;
  if (name === undefined || description === undefined) {
    throw new Error(`${path} 的 frontmatter 需要 name 与 description`);
  }
  if (!SKILL_NAME.test(name)) {
    throw new Error(`${path} 的 name "${name}" 不是 kebab-case 技能名`);
  }
  return {
    name,
    description,
    content: body.trim(),
    source: 'dsh-plugin',
    path,
    // 让 Agent 从技能目录出发解析四个 quant-*-kit 与 scripts/run.py。
    resourceBase: { kind: 'directory', path: fileURLToPath(SKILL_DIRECTORY) },
  };
}

/** 取出 YAML frontmatter 的标量字段与正文；本包的 frontmatter 只使用简单标量。 */
function splitFrontmatter(raw, path) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n?/.exec(raw);
  if (match === null) throw new Error(`${path} 缺少 YAML frontmatter`);
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const field = /^([A-Za-z][\w-]*):[ \t]*(.*)$/.exec(line);
    if (field !== null) fields[field[1]] = unquote(field[2].trim());
  }
  return { fields, body: raw.slice(match[0].length) };
}

function unquote(value) {
  const quoted = /^(?:"([\s\S]*)"|'([\s\S]*)')$/.exec(value);
  return quoted === null ? value : (quoted[1] ?? quoted[2]);
}
