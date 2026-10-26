FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --no-cache-dir \
    -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY ddi_engine_v3.py \
     ddi_operational.py \
     lambda_function.py \
     ${LAMBDA_TASK_ROOT}/

COPY chat_handler.py ${LAMBDA_TASK_ROOT}/

COPY ddi_multi.py ${LAMBDA_TASK_ROOT}/

COPY ddi_multi_chat.py ${LAMBDA_TASK_ROOT}/

COPY ddi_presentation.py ${LAMBDA_TASK_ROOT}/

COPY ddi_review_runtime.py /var/task/ddi_review_runtime.py
CMD ["chat_handler.handler"]
