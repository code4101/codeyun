"""One deterministic password policy for both interactive feedback and saving.

The score is a rule-based reference, not a crack-time or entropy guarantee.
No passwords are logged or persisted during assessment.
"""
import re
import secrets
import string


def generate_password(username: str = '') -> str:
    """Generate a 20-character CSPRNG password satisfying the same save policy."""
    alphabet = string.ascii_letters + string.digits + '!@#$%&*+-=?'
    while True:
        candidate = ''.join(secrets.choice(alphabet) for _ in range(20))
        if assess_password(candidate, username)['accepted']:
            return candidate


def assess_password(password: str, username: str = '') -> dict:
    reasons = []
    if len(password) < 10:
        reasons.append('至少使用 10 个字符')
    if len(password.encode('utf-8')) > 72:
        reasons.append('密码不能超过 72 字节')
    normalized = password.lower().translate(str.maketrans('@013457', 'aoleast'))
    letters = re.sub(r'[^a-z]', '', normalized)
    if any(word in letters for word in ('password', 'qwerty', 'admin', 'letmein', 'welcome', 'iloveyou', 'abcde', 'asdfgh', 'zxcvbn')):
        reasons.append('请避开常见密码和键盘顺序')
    if username and len(username) >= 3 and username.casefold() in password.casefold():
        reasons.append('密码不能包含账号名')
    if len(set(password)) < 5 or re.fullmatch(r'(.{1,4})\1+', password):
        reasons.append('请避免重复字符或重复短串')
    if password.isdigit():
        reasons.append('不能只使用数字')
    if any(sequence[i:i + 6] in password.lower() for sequence in ('01234567890', '09876543210', 'abcdefghijklmnopqrstuvwxyz', 'zyxwvutsrqponmlkjihgfedcba') for i in range(len(sequence) - 5)):
        reasons.append('请避免连续数字或字母')
    classes = sum((any(c.islower() for c in password), any(c.isupper() for c in password),
                   any(c.isdigit() for c in password), any(not c.isalnum() for c in password)))
    if classes < 3 and not (len(password) >= 16 and len(set(password.casefold())) >= 8):
        reasons.append('组合三类字符，或使用至少 16 字符的多词口令')
    score = 0 if not password else 1 if reasons else 4 if len(password) >= 16 else 3 if len(password) >= 12 else 2
    return {'score': score, 'label': ['未输入', '弱', '一般', '较强', '强'][score],
            'accepted': not reasons, 'reasons': reasons}
