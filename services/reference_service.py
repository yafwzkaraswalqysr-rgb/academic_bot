def generate_reference(data,style='APA'):
    title=data.get('title') or 'بدون عنوان'; author=data.get('author') or 'مؤلف غير معروف'; year=data.get('year') or 'د.ت.'; url=data.get('url') or ''
    if style=='IEEE': return f'{author}, "{title}," {year}. {url}'.strip()
    if style=='MLA': return f'{author}. "{title}." {year}. {url}'.strip()
    if style=='Chicago': return f'{author}. "{title}." ({year}). {url}'.strip()
    return f'{author}. ({year}). {title}. {url}'.strip()
