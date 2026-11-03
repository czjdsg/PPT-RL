"""Preserve literal '<image>' in recorded code/logs without treating it as an attachment."""
LITERAL_IMAGE='\ue000PPT_LITERAL_IMAGE_TAG\ue001'
def install():
    from swift.template.base import Template
    if getattr(Template,'_ppt_literal_image_compat',False):return
    original=Template._tokenize
    def tokenize(self,context,**kwargs):
        if isinstance(context,str):context=context.replace(LITERAL_IMAGE,'<image>')
        return original(self,context,**kwargs)
    Template._tokenize=tokenize
    Template._ppt_literal_image_compat=True
