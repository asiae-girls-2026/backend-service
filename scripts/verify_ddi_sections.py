#!/usr/bin/env python3
"""Offline regression tests. Does not call AWS, DB or a language model."""
import argparse
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def run(root):
    p=load(root/'ddi_presentation.py','new_presentation')
    rt=load(root/'ddi_review_runtime.py','new_review')
    sys.modules['ddi_presentation']=p
    assert p.VERSION=='20261111.7'
    backups=sorted(root.glob('qa7_backup_*'))
    old=load(backups[-1]/'ddi_presentation.py','old_presentation') if backups else None
    old_rt=load(backups[-1]/'ddi_review_runtime.py','old_review') if backups else None
    def raw(status='UNCERTAIN'):
        return {'status':status,'coverage':'INCOMPLETE','products':{
            '1':{'name':'테스트약A','item_seq':'A'},'2':{'name':'테스트약B','item_seq':'B'}},
            'official_product_evidence':[],'confirmed_rules':[],'candidate_rules':[],
            'mapping_policy':{'action_code':'TEST'},'mapping_action':'TEST'}
    def pair(value,a=1,b=2):return {'product_a':a,'product_b':b,'ddi_result':value}
    empty=raw();missing=raw('UNRESOLVED_MAPPING')
    reference=raw();reference['candidate_rules']=[{'type':'INGREDIENT_DUR_RULE',
        'evidence':{'ingredient_a':'테스트성분A','ingredient_b':'테스트성분B','prohibition_content':'미승인 참고 원문'}}]
    approved=raw('MATCHED_RULE');approved['confirmed_rules']=[{
        'active':True,'scope_verified':True,'review_status':'APPROVED',
        'type':'INGREDIENT_DUR_RULE','prohibition_reasons':['검증된 주의 원문 전체'],
        'dur_codes':['A','B']}]
    official=raw('CONFIRMED_DUR_PRODUCT_MATCH')
    official['official_product_evidence']=[{'source':'MFDS_DUR','product_ids':[1,2],
        'item_seqs':['A','B'],'dur_seq':'TEST','prohibition_reason':'공식 병용금기 원문 전체',
        'verification':{'level':'SOURCE_ITEM_PAIR_MATCH','checks':{
            'type_code_a':True,'item_seq_match':True,'mixture_item_seq_match':True}}}]
    rejected=deepcopy(official);rejected['official_product_evidence'][0]['verification']['checks']['item_seq_match']=False
    inactive=deepcopy(approved);inactive['confirmed_rules'][0]['active']=False
    scope=deepcopy(approved);scope['confirmed_rules'][0]['scope_verified']=False
    no_reason=deepcopy(approved);no_reason['confirmed_rules'][0]['prohibition_reasons']=[]
    cases=[('no evidence',[pair(empty)]),('missing mapping',[pair(missing)]),
        ('reference',[pair(reference)]),('approved',[pair(approved)]),('official',[pair(official)]),
        ('rejected',[pair(rejected)]),('inactive',[pair(inactive)]),('scope',[pair(scope)]),
        ('missing reason',[pair(no_reason)]),('mixed',[pair(approved),pair(reference,1,3)]),
        ('empty',[])]
    for label,pairs in cases:
        snapshot=deepcopy(pairs);result=p.present_pairs(pairs,'INCOMPLETE')
        assert pairs==snapshot, label+' mutated inputs'
        assert result['ddi_sections_version']==1
        sections=result['ddi_sections']
        assert sections and sections[-1]['type']=='CAUTION'
        for section in sections:
            assert section['type'] in {'EVIDENCE','RESULT','CAUTION'}
            assert section['title'] and section['items']
            assert all(isinstance(x,str) and x.strip() for x in section['items'])
            assert len(section['items'])==len(set(section['items']))
        evidence=[s for s in sections if s['type']=='EVIDENCE']
        caution=sections[-1]['items']
        assert set(result['limitations'])<=set(caution)
        assert all('자료에 적힌' not in item for s in sections for item in s['items'])
        assert sum('DUR 국민 체험관' in x for s in sections for x in s['items'])<=1
        if label not in {'approved','official','missing reason','mixed'}:
            assert not evidence,label+' promoted unverified evidence'
        if label in {'approved','mixed'}:
            assert evidence[0]['items']==['검증된 주의 원문 전체']
        if label=='official':assert evidence[0]['items']==['공식 병용금기 원문 전체']
        if label=='reference':assert all('미승인 참고 원문' not in x for s in sections for x in s['items'])
        if old:
            before=old.present_pairs(pairs,'INCOMPLETE')
            after={k:v for k,v in result.items() if k not in {'ddi_sections','ddi_sections_version'}}
            before.pop('presentation_version');after.pop('presentation_version')
            assert before==after,label+' changed previous answer/status/evidence'
        print('PASS sections / unchanged answer and judgment:',label)
    captured={'1:2':{'product_ids':[1,2],'product_names':['테스트약A','테스트약B'],'ddi_result':approved},
              '1:3':{'product_ids':[1,3],'product_names':['테스트약A','테스트약C'],'ddi_result':reference}}
    def response_for(module,presentation):
        sys.modules['ddi_presentation']=presentation
        def original(event,context):
            module.CAPTURE.get().update(deepcopy(captured))
            return {'answer':'placeholder','message':'placeholder','coverage':'INCOMPLETE',
                'assessment_status':'BASE','evidence_summary':{},'pair_assessments':[],
                'pair_results':[dict(pair(approved),status='MATCHED_RULE',coverage='INCOMPLETE'),
                                dict(pair(reference,1,3),status='UNCERTAIN',coverage='INCOMPLETE')]}
        return module.wrap_handler(original)({'mode':'ddi_multi_chat'},None)
    after=response_for(rt,p)
    assert after['presentation_version']==p.VERSION
    assert any(s['type']=='EVIDENCE' for s in after['ddi_sections'])
    assert any(s['type']=='RESULT' for s in after['ddi_sections'])
    assert after['ddi_sections'][-1]['type']=='CAUTION'
    assert rt.CAPTURE.get() is None
    if old_rt:
        before=response_for(old_rt,old)
        for key in before:assert before[key]==after[key], 'wrapper changed '+key
    sys.modules['ddi_presentation']=p
    print('PASS final approval wrapper / mixed pairs / plain-answer compatibility')
    print('ALL DDI SECTION CHECKS PASSED')
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--task-root',type=Path,default=Path(__file__).resolve().parents[1]/'lambda/medication')
    run(parser.parse_args().task_root)
