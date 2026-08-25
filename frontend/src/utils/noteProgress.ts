export const NOTE_COMPLETION_PROGRESS_EXPR_FIELD = '__completion_progress_expr';

type GenericCustomFieldTuple = [string, string, unknown];

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export const isNoteSystemCustomFieldKey = (key: unknown) => typeof key === 'string' && key.startsWith('__');

export const normalizeCompletionProgressExpr = (value: unknown) => {
  if (value == null) return '';
  return String(value).trim();
};

class ProgressExpressionParser {
  private readonly text: string;
  private index = 0;

  constructor(text: string) {
    this.text = text;
  }

  parse() {
    const value = this.parseExpression();
    this.skipWhitespace();
    if (this.index < this.text.length) throw new Error('Unexpected token');
    return value;
  }

  private parseExpression(): number {
    let value = this.parseTerm();
    while (true) {
      this.skipWhitespace();
      if (this.consume('+')) {
        value += this.parseTerm();
        continue;
      }
      if (this.consume('-')) {
        value -= this.parseTerm();
        continue;
      }
      return value;
    }
  }

  private parseTerm(): number {
    let value = this.parseFactor();
    while (true) {
      this.skipWhitespace();
      if (this.consume('*')) {
        value *= this.parseFactor();
        continue;
      }
      if (this.consume('/')) {
        const divisor = this.parseFactor();
        if (divisor === 0) throw new Error('Division by zero');
        value /= divisor;
        continue;
      }
      return value;
    }
  }

  private parseFactor(): number {
    this.skipWhitespace();

    if (this.consume('+')) return this.parseFactor();
    if (this.consume('-')) return -this.parseFactor();

    if (this.consume('(')) {
      const value = this.parseExpression();
      this.skipWhitespace();
      if (!this.consume(')')) throw new Error('Missing closing parenthesis');
      return this.parsePercentSuffix(value);
    }

    const value = this.parseNumber();
    return this.parsePercentSuffix(value);
  }

  private parsePercentSuffix(value: number) {
    this.skipWhitespace();
    if (this.consume('%')) return value / 100;
    return value;
  }

  private parseNumber(): number {
    this.skipWhitespace();
    const start = this.index;

    while (this.index < this.text.length && /[0-9.]/.test(this.text[this.index]!)) {
      this.index += 1;
    }

    if (start === this.index) throw new Error('Number expected');

    if (this.index < this.text.length && /[eE]/.test(this.text[this.index]!)) {
      const exponentStart = this.index;
      this.index += 1;
      if (this.index < this.text.length && /[+-]/.test(this.text[this.index]!)) {
        this.index += 1;
      }
      const digitStart = this.index;
      while (this.index < this.text.length && /[0-9]/.test(this.text[this.index]!)) {
        this.index += 1;
      }
      if (digitStart === this.index) {
        this.index = exponentStart;
      }
    }

    const parsed = Number(this.text.slice(start, this.index));
    if (!Number.isFinite(parsed)) throw new Error('Invalid number');
    return parsed;
  }

  private skipWhitespace() {
    while (this.index < this.text.length && /\s/.test(this.text[this.index]!)) {
      this.index += 1;
    }
  }

  private consume(token: string) {
    if (this.text.startsWith(token, this.index)) {
      this.index += token.length;
      return true;
    }
    return false;
  }
}

export const evaluateCompletionProgressExpr = (value: unknown) => {
  const text = normalizeCompletionProgressExpr(value);
  if (!text) return null;

  try {
    const parsed = new ProgressExpressionParser(text).parse();
    if (!Number.isFinite(parsed)) return null;
    return Math.min(1, Math.max(0, parsed));
  } catch {
    return null;
  }
};

export const isDefaultFullCompletionProgressExpr = (value: unknown) => {
  const text = normalizeCompletionProgressExpr(value).replace(/\s+/g, '');
  return /^(?:1(?:\.0+)?|100(?:\.0+)?%)$/.test(text);
};

const toCustomFieldTuples = (customFields: unknown): GenericCustomFieldTuple[] => {
  if (Array.isArray(customFields)) {
    return customFields.flatMap(item => {
      if (Array.isArray(item) && item.length >= 3 && typeof item[0] === 'string') {
        return [[item[0], String(item[1] ?? 'string'), item[2]] as GenericCustomFieldTuple];
      }
      if (item && typeof item === 'object' && typeof (item as any).key === 'string') {
        return [[(item as any).key, String((item as any).type ?? 'string'), (item as any).value] as GenericCustomFieldTuple];
      }
      return [];
    });
  }

  if (customFields && typeof customFields === 'object') {
    return Object.entries(customFields as Record<string, unknown>).map(([key, value]) => {
      const type = typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'string';
      return [key, type, value] as GenericCustomFieldTuple;
    });
  }

  return [];
};

export const getCompletionProgressExprFromCustomFields = (customFields: unknown) => {
  const match = toCustomFieldTuples(customFields).find(([key]) => key === NOTE_COMPLETION_PROGRESS_EXPR_FIELD);
  return normalizeCompletionProgressExpr(match?.[2]);
};

export const upsertCompletionProgressExprInCustomFields = (
  customFields: unknown,
  expr: unknown
): GenericCustomFieldTuple[] => {
  const normalizedExpr = normalizeCompletionProgressExpr(expr);
  const nextFields = toCustomFieldTuples(customFields).filter(([key]) => key !== NOTE_COMPLETION_PROGRESS_EXPR_FIELD);
  if (normalizedExpr) nextFields.push([NOTE_COMPLETION_PROGRESS_EXPR_FIELD, 'string', normalizedExpr]);
  return nextFields;
};

export const stripNoteSystemCustomFields = (customFields: unknown): GenericCustomFieldTuple[] => (
  toCustomFieldTuples(customFields).filter(([key]) => !isNoteSystemCustomFieldKey(key))
);

export const resolveCompletionProgressFillRatio = (params: {
  lifecycleStage?: string | null;
  completionProgress?: unknown;
  completionProgressExpr?: unknown;
  customFields?: unknown;
}) => {
  const normalizedStage = String(params.lifecycleStage || '').trim().toLowerCase() === 'predone'
    ? 'done'
    : String(params.lifecycleStage || '').trim().toLowerCase();
  const rawExpr = normalizeCompletionProgressExpr(
    params.completionProgressExpr ?? getCompletionProgressExprFromCustomFields(params.customFields)
  );
  const explicitRatio = isFiniteNumber(params.completionProgress)
    ? Math.min(1, Math.max(0, params.completionProgress))
    : evaluateCompletionProgressExpr(rawExpr);

  if (normalizedStage === 'done') {
    return explicitRatio ?? 1;
  }
  return explicitRatio;
};
