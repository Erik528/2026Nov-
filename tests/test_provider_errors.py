"""Error-parser unit tests; not live API calls or simulated successful processing."""
import httpx
import pytest
from app.provider import response_error


def response(code=None,kind=None,status=429,headers=None,message='upstream private message'):
    return httpx.Response(status,json={'error':{'code':code,'type':kind,'message':message}},headers=headers)


@pytest.mark.parametrize('code',['insufficient_quota','billing_hard_limit_reached','billing_not_active'])
def test_quota_is_not_reported_as_temporary_throttle(code):
    error=response_error(response(code,'insufficient_quota'),'/chat/completions','gpt-4.1')
    assert error.details['category']=='quota'
    assert 'ChatGPT' in str(error)
    assert '等待重试通常不能解决' in str(error)


def test_rate_limit_contains_safe_retry_and_model_diagnostics():
    error=response_error(response('rate_limit_exceeded','tokens',headers={
        'retry-after':'12','x-request-id':'req_0123456789abcdef',
        'x-ratelimit-limit-tokens':'30000','x-ratelimit-remaining-tokens':'0',
        'x-ratelimit-reset-tokens':'1m2.5s'}),'/images/edits','gpt-image-1')
    assert error.details['category']=='rate_limit'
    assert error.details['retry_after_seconds']==12
    assert error.details['rate_limits']['remaining-tokens']=='0'
    assert error.details['model']=='gpt-image-1'
    assert '余额' in str(error) and '12' in str(error)


def test_unknown_429_does_not_guess_credit_exhaustion():
    error=response_error(httpx.Response(429,text='non-json body'),'/chat/completions')
    assert error.details['category']=='unknown_429'
    assert '暂不能区分' in str(error)


def test_no_secret_or_private_body_is_exposed():
    secret='sk-proj-private-diagnostic-test-1234567890'
    error=response_error(response(secret,secret,headers={
        'x-request-id':secret,'retry-after':secret,'x-ratelimit-reset-tokens':secret,
        'x-ratelimit-limit-requests':secret},message=secret),'/invalid/'+secret,secret)
    assert secret not in str(error)
    assert secret not in repr(error.details)
    assert error.details['code']=='unknown'
    assert error.details['endpoint']=='unknown'


def test_array_error_body_is_handled():
    error=response_error(httpx.Response(429,json={'error':['bad']}),'/images/edits')
    assert error.details['category']=='unknown_429'


def test_image_model_unavailable_reports_exact_parameter():
    r=httpx.Response(400,json={'error':{'code':'invalid_value','type':'image_generation_user_error',
        'param':'model','message':"The model 'gpt-image-1' does not exist."}})
    error=response_error(r,'/images/edits','gpt-image-1')
    assert error.details['category']=='model_unavailable'
    assert error.details['parameter']=='model'
    assert error.details['code']=='invalid_value'
    assert 'gpt-image-2.5-sunburst' in str(error)


def test_organization_verification_has_actionable_message():
    r=httpx.Response(400,json={'error':{'type':'image_generation_user_error',
        'message':'Your organization must be verified to use this model. private content'}})
    error=response_error(r,'/images/edits','gpt-image-2.5-sunburst')
    assert error.details['category']=='organization_verification'
    assert 'Verify Organization' in str(error)
    assert 'private content' not in str(error)


def test_unknown_input_error_identifies_mask_without_echoing_body():
    r=httpx.Response(400,json={'error':{'code':'invalid_value','param':'mask','message':'private customer image data'}})
    error=response_error(r,'/images/edits')
    assert error.details['parameter']=='mask'
    assert 'private customer image data' not in str(error)


def test_default_image_model_is_current(monkeypatch):
    from app.provider import settings
    monkeypatch.delenv('AD_IMAGE_MODEL',raising=False)
    assert settings()['image']=='gpt-image-2.5-sunburst'


def test_retired_model_is_rejected_before_paid_request(monkeypatch):
    from app.provider import OpenAIImages,ProviderError
    from PIL import Image
    monkeypatch.setenv('AD_OPENAI_API_KEY','not-a-real-key')
    monkeypatch.setenv('AD_IMAGE_MODEL','gpt-image-1')
    api=OpenAIImages()
    with pytest.raises(ProviderError,match='停用'):
        api.edit(Image.new('RGBA',(1024,1024)),Image.new('RGBA',(1024,1024)),'background')
