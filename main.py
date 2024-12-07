import requests
import json
import sqlite3
import datetime
from openai import OpenAI
from anthropic import Anthropic
import random
import types
import time
import gspread
import httpx
from dotenv import load_dotenv
import os
from database_functions import create_database


pact_private_token = os.getenv('PACT_PRIVATE_TOKEN')
proxy_url = os.getenv('PROXY_URL')
rev_ai_token = os.getenv('REV_AI_TOKEN')
company_id = os.getenv('COMPANY_ID')
openAi_API_key = os.getenv('OPENAI_API_KEY')
claude_API_key = os.getenv('CLAUDE_API_KEY')
gs_table_name = 'Анализ диалогов'

current_day_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

def add_conversation_to_database(conversation):
    if len(conversation['contacts']) == 0:
        return False

    chat_type = 'Личная переписка' if len(conversation['contacts']) == 2 else 'Рабочий чат'
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = f"""INSERT INTO conversations VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        cursor.execute(query_str, (
            conversation['external_id'],
            conversation['channel_id'],
            conversation['channel_type'],
            conversation['name'],
            conversation['sender_external_id'],
            conversation['created_at'],
            conversation['created_at_timestamp'],
            conversation['contacts'][0]['external_id'],
            conversation['contacts'][0]['external_public_id'],
            conversation['contacts'][0]['name'],
            company_id,  # пока одна компания глобальной переменной
            chat_type
        ))
        pact_database.commit()

    return True

def add_messages_to_database(messages, conversation_external_id):
    if len(messages) == 0:
        return False

    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        data = []
        for message in messages:

            message_to_add = message['message']
            if len(message['attachments']) > 0:
               if message['attachments'][0]['url'].endswith('.ogg'):
                   try:
                       # message_to_add = get_text_from_audio_message(message['attachments'][0]['url']) # замена на rev.ai
                       message_to_add = get_text_from_audio_message_rev_ai(message['attachments'][0]['url'])
                   except Exception as e:
                       print(e.add_note('ошибка транскрибации голосового сообщения'))

            data.append(
                (
                    message['external_id'],
                    message['channel_id'],
                    message['channel_type'],
                    message_to_add,
                    message['income'],
                    message['created_at'],
                    message['created_at_timestamp'],
                    message['attachments'][0]['external_id'] if len(message['attachments']) > 0 else None,
                    message['attachments'][0]['url'] if len(message['attachments']) > 0 else None,
                    conversation_external_id,
                )
            )

        query_str = """INSERT INTO messages VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        cursor.executemany(query_str, data)
        pact_database.commit()

    return True

def analyze_conversation(dialogue_for_analysing, prompt):
    # ОТПРАВЛЯЕМ ВСЕ СООБЩЕНИЯ В ГПТ И ЖДЕМ РЕЗУЛЬТАТ
    # Результат анализа внести в новую таблицу в базе данных ???
    #   (conversation_id, date_of_analysing, number_of_messages, text_result)

    #сделать разветвление для ОпенАи и для Клауда

    if prompt[5] == 'openai':

        #api_key = os.environ.get("openAi_API_key") # это можно использовать, если добавить токен опенАи в Path системы
        #client = OpenAI(api_key=openAi_API_key)
        client = OpenAI(api_key=openAi_API_key, http_client=httpx.Client(proxy=proxy_url))

        chat_completion = client.chat.completions.create(
            messages=
            [
                # {
                #     "role": "system",
                #     "content": prompt[0]
                # },
                {
                    "role": "user",
                    "content": prompt[0] + '\n' + dialogue_for_analysing,
                    #"content": 'Переписка для анализа - \n' + dialogue_for_analysing + '\n' + prompt[0],
                }
            ],
            model=prompt[6]#,response_format={"type": "json_object"}
        )

        content = chat_completion.choices[0].message.content

    else: # it's claude
        client = Anthropic(api_key=claude_API_key, http_client=httpx.Client(proxy=proxy_url))

        #message = client.beta.prompt_caching.messages.create(
        message = client.messages.create(
            max_tokens=1024,
            #system=prompt[0],  # <-- role prompt
            # system = [
            #     {
            #         "type": "text",
            #         "text": "Ты АИ ассистент по анализу переписок.\n"
            #     },
                # {
                #     "type": "text",
                #     "text": dialogue_for_analysing,
                #     "cache_control": {"type": "ephemeral"}
                # }
            # ],
            messages=[
                {
                    "role": "user",
                    "content": prompt[0] + '\n' + dialogue_for_analysing,
                    #"content": prompt[0],
                }
            ],
            model=prompt[6],
            #extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}  # Custom headers here
        )

        content = message.content[0].text

    #record_analysis_data_to_db(conversation_external_id, len(list_of_messages), content, prompts)

    return content

def analyzing_main():

    # открываем таблицу
    #gc = gspread.oauth()
    gc = gspread.service_account()
    sh = gc.open(gs_table_name)

    # получаем таблицу диалогов (отфильтровываем нерабочие???)
    ws = sh.sheet1
    values_list = ws.col_values(4)
    len_row = len(values_list) + 1
    range_name_dialogues = 'A6:AC' + str(len_row)
    dialogues_table = ws.get(range_name=range_name_dialogues)

    # получаем таблицу промптов
    ws_prompts = sh.worksheet("prompts")
    prompts_len_row = len(ws_prompts.col_values(1)) + 1
    range_name_prompts = 'A3:N' + str(prompts_len_row)
    prompts_table = ws_prompts.get(range_name=range_name_prompts)

    # отбираем актуальные промпты
    allowed_to_analyse_prompts = []
    for prompt in prompts_table:
        if prompt[3] == 'Да':
            allowed_to_analyse_prompts.append(prompt)

    if len(allowed_to_analyse_prompts) == 0:
        print('No prompts to analyse!')
        return

    # отбираем промпты для личных бесед
    prompts_for_private_chats = []
    for prompt in allowed_to_analyse_prompts:
        if 'Личная переписка' in prompt[4]:
            prompts_for_private_chats.append(prompt)

    # отбираем промпты для рабочих чатов
    prompts_for_work_chat = []
    for prompt in allowed_to_analyse_prompts:
        if 'Рабочий чат' in prompt[4]:
            prompts_for_work_chat.append(prompt)

    for dialogue in dialogues_table:

        max_index_of_cells = len(dialogue)

        # проверяем какой список промптов ему подходит
        prompts_for_current_chat = prompts_for_private_chats if dialogue[3] == 'Личная переписка' else prompts_for_work_chat

        conversation_id = get_conversation_id_with_telephone(dialogue[5])
        messages_of_conversation = get_messages_of_conversation_db(conversation_id)
        dialogue_for_analysing = get_dialogue_by_roles_from_messages(messages_of_conversation, dialogue[1])
        current_cell = ws.find(dialogue[5])

        # формируем множество уникальных наборов нейросеть+модель, чтобы далее использовать кэш для тех,
        # у которых одинаковые параметры нейронки
        groups_of_prompts = set()
        for prompt in prompts_for_current_chat:
            groups_of_prompts.add(prompt[5]+prompt[6])

        for group in groups_of_prompts:

            # для каждой группы собираем список промптов
            prompts_for_current_chat_and_group = []
            for prompt in prompts_for_current_chat:
                if group == prompt[5]+prompt[6]:
                    prompts_for_current_chat_and_group.append(prompt)

            for prompt in prompts_for_current_chat_and_group:
                index_of_date_of_analysing = int(prompt[2]) + 1

                # ПРОВЕРКА УСЛОВИЙ ПО ДАТАМ ПЕРЕД ВЫПОЛНЕНИЕМ ПРОМПТА. ИХ МОЖНО ДОПОЛНЯТЬ
                if prompt[8] == 'постоянно':
                    if not index_of_date_of_analysing > max_index_of_cells:
                        last_prompt_analyzing_date_str = dialogue[index_of_date_of_analysing]
                        if not len(last_prompt_analyzing_date_str) == 0:
                            last_prompt_analyzing_date = datetime.datetime.strptime(last_prompt_analyzing_date_str,
                                                                                    "%d.%m.%Y")
                            next_analysing_date = last_prompt_analyzing_date + datetime.timedelta(days=int(prompt[9]))
                            if next_analysing_date > current_day_start: # если не наступил день следующего анализа
                                continue
                elif prompt[8] == 'нет новых сообщений':
                    last_message_of_dialogue_date_str = get_last_message_of_conversation_date(conversation_id)
                    last_message_of_dialogue_date = datetime.datetime.strptime(last_message_of_dialogue_date_str[:10],
                                                                               "%Y-%m-%d")
                    next_analysing_date = last_message_of_dialogue_date + datetime.timedelta(days=int(prompt[9]))
                    if next_analysing_date > current_day_start:
                        continue
                elif prompt[8] == 'один раз':
                    if not index_of_date_of_analysing > max_index_of_cells:
                        last_analyzing_date_of_prompt = dialogue[index_of_date_of_analysing]
                        if not len(last_analyzing_date_of_prompt) == 0: # значит уже один раз анализировали
                            continue
                        else:
                            dialogue_added_date_str = dialogue[2]
                            dialogue_added_date = datetime.datetime.strptime(dialogue_added_date_str,"%d.%m.%Y")
                            next_analysing_date = dialogue_added_date + datetime.timedelta(days=int(prompt[9]))
                            if next_analysing_date > current_day_start: # если не наступил день следующего анализа
                                continue
                else:
                    print('Не определено условие по промпту - ' + prompt[0][0:20])
                    continue

                text_result = analyze_conversation(dialogue_for_analysing, prompt)
                #short_result = 'short_result_passed' # Нужно получить его из полного. Видимо начало ответа надо делать Y/N
                current_datetime = datetime.datetime.now()

                column_to_write = prompt[2]
                #ws.update_cell(current_cell.row, int(column_to_write), short_result)
                ws.update_cell(current_cell.row, int(column_to_write) + 1, text_result)
                ws.update_cell(current_cell.row, int(column_to_write) + 2,
                                           str(current_datetime.day) + '.'
                                           + str(current_datetime.month) + '.'
                                           + str(current_datetime.year))
                #time.sleep(1) # можно не делать паузу т.к. на этапе анализа в гпт итак происходит пауза 2-3 секунды

    print('analysing done')

def conversation_exists(conversation_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM conversations WHERE external_id = ?"""
        cursor.execute(query_str, (conversation_id,))
        list_of_conversations = cursor.fetchall()
        pact_database.commit()

        if len(list_of_conversations) > 0:
            return True
        else:
            return False

def conversation_is_analyzed(conversation_external_id, number_of_conversation_messages_db):
    ### ЗАПРОС К НОВОЙ ТАБЛИЦЕ analysis (conversation_id, date_of_analysing, number_of_messages, text_result)
    ### если есть то False else True
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM analysis WHERE conversation_external_id = ? AND number_of_messages = ?"""
        cursor.execute(query_str, (conversation_external_id, number_of_conversation_messages_db))
        list_of_messages = cursor.fetchall()
        pact_database.commit()

        return True if len(list_of_messages) > 0 else False

def get_dialogue_by_roles_from_messages(messages_of_conversation, chat_type):

    conversation_role = 'Сообщение' # default на случай если это рабочий чат, в котором невозможно установить роль

    #ОБХОДИМ СПИСОК СООБЩЕНИЙ И СОСТАВЛЯЕМ ТЕКСТ В ФОРМЕ ДИАЛОГА ПО РОЛЯМ
    dialogue_for_analysing = ""
    for message in messages_of_conversation:
        if chat_type == 'Личная переписка':
            if message[4] == 1:
                conversation_role = 'Клиент'
            else:
                conversation_role = 'Менеджер'

        if type(message[3]) != types.NoneType: # Например, если сообщение это картинка, то поле будет NoneType

            dialogue_for_analysing = (dialogue_for_analysing
                                  + conversation_role + ': ' + message[3] + "\n")

    return dialogue_for_analysing

def get_conversations_of_company(company_id):
    headers = {'X-Private-Api-Token': pact_private_token}
    url_conversation = f'https://api.pact.im/p1/companies/{company_id}/conversations?per=100'
    request_conversations = requests.get(url=url_conversation, headers=headers)
    data_of_request = json.loads(request_conversations.text)['data']
    conversations = data_of_request['conversations']

    while 'next_page' in data_of_request:
        from_page_id = json.loads(request_conversations.content)['data']['next_page']
        url_conversation = f'https://api.pact.im/p1/companies/{company_id}/conversations?from={from_page_id}&per=100'
        request_conversations = requests.get(url=url_conversation, headers=headers)
        data_of_request = json.loads(request_conversations.text)['data']
        additional_conversations = data_of_request['conversations']
        conversations += additional_conversations

    return conversations

def get_conversation_details(company_id, conversation_id):
    headers = {'X-Private-Api-Token': pact_private_token}
    url_conversation = f'https://api.pact.im/p1/companies/{company_id}/conversations/{conversation_id}'
    request_conversations = requests.get(url=url_conversation, headers=headers)
    #conversations = json.loads(request_conversations.text)['data']['conversations']
    return request_conversations

def get_conversations_of_company_db(company_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM conversations WHERE company_id = ?"""
        cursor.execute(query_str, (company_id,))
        list_of_conversations = cursor.fetchall()
        pact_database.commit()

        return list_of_conversations

def get_dialogue_from_list_of_messages(list_of_messages, chat_type):
    dialogue_for_analysing = ""
    for message in list_of_messages:
        if chat_type == 'Личная переписка':
            if message[4] == 1:
                role = 'Клиент'
            else:
                role = 'Менеджер'
        else:
            role = 'Message'  # НОМЕР ТЕЛЕФОНА !!!!!!!!!!!!!!!

        if type(message[3]) != types.NoneType:
            dialogue_for_analysing = (dialogue_for_analysing
                                      + role + ': ' + message[3] + "\n")

    return dialogue_for_analysing

def get_messages_of_conversation(company_id, conversation_id):
    headers = {'X-Private-Api-Token': pact_private_token}

    url_message = (f'https://api.pact.im/p1/companies/{company_id}/conversations/{conversation_id}/'
                       f'messages?per=100')

    try:
        request_messages = requests.get(url=url_message, headers=headers, timeout=10)
        data_of_request = json.loads(request_messages.text)['data']
        messages_from_request = data_of_request['messages']

        while 'next_page' in data_of_request:
            from_page_id = json.loads(request_messages.content)['data']['next_page']
            url_message = (f'https://api.pact.im/p1/companies/{company_id}/conversations/{conversation_id}/'
                           f'messages?from={from_page_id}&per=100')
            request_messages = requests.get(url=url_message, headers=headers)
            data_of_request = json.loads(request_messages.text)['data']
            additional_messages = data_of_request['messages']
            messages_from_request += additional_messages

    except requests.exceptions.ReadTimeout:
        print("\n Timeout occurred \nCan't get messages from" + str(conversation_id))
        messages_from_request = []
        time.sleep(3)

    #messages_from_request = json.loads(request_messages.text)['data']['messages']
    #sleep(3) # чтобы не спамить запросами в пакт (?)

    return messages_from_request

def get_messages_of_conversation_db(conversation_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM messages WHERE conversation_external_id = ? ORDER BY created_at_timestamp DESC LIMIT 100"""
        cursor.execute(query_str, (conversation_id,))
        list_of_messages = cursor.fetchall()
        pact_database.commit()

        return list_of_messages

def get_new_messages_for_database(messages, conversation_external_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM messages WHERE conversation_external_id = ?"""
        cursor.execute(query_str, (conversation_external_id,))
        list_of_messages_db = cursor.fetchall()

        new_messages_for_database = []

        for message in messages:
            message_exists = False
            for message_db in list_of_messages_db:
                if message_db[0] == message['external_id']:
                    message_exists = True
                    break
            if message_exists == 0:
                new_messages_for_database.append(message)

        pact_database.commit()

        return new_messages_for_database

def get_number_of_conversation_messages_db(conversation_external_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * FROM messages WHERE conversation_external_id = ?"""
        cursor.execute(query_str, (conversation_external_id,))
        list_of_messages = cursor.fetchall()
        pact_database.commit()

        return len(list_of_messages)

def get_text_from_audio_message(audio_message_url):
    headers = {
        'Content-Type': 'application/json',
    }
    ##скачиваем аудио
    req = requests.get(url=audio_message_url, headers=headers)

    audio_name = ''.join(random.choices('0123456789', k=9)) + '.ogg'

    with open(audio_name, 'wb') as audio_message:
        audio_message.write(req.content)

    ## отправляем в гпт на транскрибацию
    client = OpenAI(api_key=openAi_API_key)
    # api_key=os.environ.get("openAi_API_key") # это можно использовать, если добавить токен опенАи в Path системы
    audio_file = open(audio_name, 'rb')
    audio_transcription = client.audio.transcriptions.create(
        file=audio_file,
        model="whisper-1",
        language="ru",
    )

    return audio_transcription.text

def get_text_from_audio_message_rev_ai(audio_message_url):

    headers_post = {
        'Authorization': 'Bearer ' + rev_ai_token,
        'Content-Type': 'application/json'
    }

    data = {
        'source_config': {
            'url': audio_message_url
        },
        "language": "ru",
        'metadata':'This is a test'
    }

    req_post = requests.post(url=r'https://api.rev.ai/speechtotext/v1/jobs', headers=headers_post, data=json.dumps(data))
    file_id = json.loads(req_post.text)['id']

    headers_status = {
        'Authorization': 'Bearer ' + rev_ai_token
    }

    headers_get_text = {
        'Authorization': 'Bearer ' + rev_ai_token,
        'Accept': 'application/vnd.rev.transcript.v1.0+json'
    }

    time.sleep(20)

    for x in range(3):
        req_get_status = requests.get(url='https://api.rev.ai/speechtotext/v1/jobs/' + file_id, headers=headers_status)
        status = json.loads(req_get_status.text)['status']

        if status == 'transcribed':

            headers_get_plaintext = {
                'Authorization': 'Bearer ' + rev_ai_token,
                'Accept': 'text/plain'
            }
            req_get_text = requests.get(url='https://api.rev.ai/speechtotext/v1/jobs/' + file_id + '/transcript',
                                        headers=headers_get_plaintext)
            text_from_voice = req_get_text.text[25:]
            return text_from_voice
        else:
            time.sleep(20)
    return ''

def get_text_of_conversation(conversation_external_id):
    # ОТПРАВЛЯЕМ ВСЕ СООБЩЕНИЯ В ГПТ И ЖДЕМ РЕЗУЛЬТАТ
    # Результат анализа внести в новую таблицу в базе данных
    #   (conversation_id, date_of_analysing, number_of_messages, text_result)
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT * 
                    FROM messages LEFT JOIN main.conversations c on messages.conversation_external_id = c.external_id 
                    WHERE messages.conversation_external_id = ? 
                    ORDER BY messages.created_at_timestamp DESC"""
        cursor.execute(query_str, (conversation_external_id,))
        list_of_messages = cursor.fetchall()
        pact_database.commit()

        #ОБХОДИМ СПИСОК СООБЩЕНИЙ И СОСТАВЛЯЕМ ЕДИНЫЙ ТЕКСТ И ОТПРАВЛЯЕМ В ГПТ
        dialogue_for_analysing = ""
        for message in list_of_messages:
            if message[4] == 1:
                conversation_role = 'Клиент'
            else:
                conversation_role = 'Менеджер'

            if type(message[3]) != types.NoneType:
                dialogue_for_analysing = (dialogue_for_analysing
                                      + conversation_role + ': ' + message[3] + "\n")

        return dialogue_for_analysing

def get_conversation_id_with_telephone(telephone):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT external_id FROM conversations WHERE sender_external_id = ?"""
        cursor.execute(query_str, (telephone,))
        string_of_table = cursor.fetchone()
        pact_database.commit()

        return string_of_table[0] # external_id

def get_last_analysing_of_conversation_date(conversation_id, text_prompt):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT date_of_analyzing_timestamp FROM analysis WHERE conversation_external_id = ? AND text_prompt = ? ORDER BY date_of_analyzing_timestamp DESC"""
        cursor.execute(query_str, (conversation_id, text_prompt,))
        string_of_table = cursor.fetchone()
        pact_database.commit()

        return string_of_table[0] if not string_of_table is None else 0 # date_of_analyzing_timestamp

def get_last_message_of_conversation_date(conversation_id):
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = """SELECT created_at FROM messages WHERE conversation_external_id = ? Order by created_at_timestamp DESC"""
        cursor.execute(query_str, (conversation_id,))
        string_of_table = cursor.fetchone()
        pact_database.commit()

        return string_of_table[0]  # created_at_timestamp

def record_analysis_data_to_db(conversation_external_id, number_of_messages, text_result, text_prompt):
    #unique_id = uuid.uuid1().int
    unique_id = ''.join(random.choices('0123456789', k=16))

    current_datetime = datetime.datetime.now()
    date_of_analyzing = current_datetime.isoformat(timespec='milliseconds') + 'Z' #UTC format
    date_of_analyzing_timestamp = int(current_datetime.timestamp())

    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        query_str = f"""INSERT INTO analysis VALUES(?, ?, ?, ?, ?, ?, ?)"""
        cursor.execute(query_str, (
            unique_id,
            conversation_external_id,
            date_of_analyzing,
            date_of_analyzing_timestamp,
            number_of_messages,
            text_result,
            text_prompt
        ))
        pact_database.commit()

    return True

def update_conversations_list():

    #gc = gspread.oauth() # он вызывает открытие браузера и авторизацию через логин и пароль, а нам это неудобно
    gc = gspread.service_account()

    sh = gc.open(gs_table_name)
    ws = sh.sheet1
    values_list = ws.col_values(6) #все значения колонки Номер/id

    current_row = len(values_list) + 1
    list_of_rows = []
    list_of_new_conversations = []

    list_of_conversations = get_conversations_of_company_db(company_id)
    for conversation in list_of_conversations:
        #result_of_analyzing = analyze_conversation(conversation[0], conversation[11])
        if not conversation[4] in values_list:
            list_of_new_conversations.append(conversation)
            ## определить индекс последней строки и создать на следующей строке запись
            adding_date = str(current_day_start.day) + '.'+ str(current_day_start.month) + '.' + str(current_day_start.year)
            current_row_list = ['да', conversation[2], adding_date, conversation[11], conversation[3], conversation[4],
                                r'https://msg.pact.im/messages?operational_state=open'
                                r'&current_conversation_id=' + str(conversation[0])]
            list_of_rows.append(current_row_list)

    range_name = 'A' + str(current_row) + ':G' + str(current_row+len(list_of_new_conversations) - 1)
    ws.update(values=list_of_rows, range_name=range_name)

    print('conversations_list updated')

def update_data_base():
    ### 1 Получить все диалоги
    conversations = get_conversations_of_company(company_id)

    ### 2 Обойти все диалоги и их сообщения
    for conversation in conversations:
        messages = get_messages_of_conversation(company_id, conversation['external_id'])
        # если такой чат уже есть в базе, проверяем
        if conversation_exists(conversation['external_id']):
            number_of_conversation_messages = len(messages)
            number_of_conversation_messages_db = get_number_of_conversation_messages_db(conversation['external_id'])

            # если количество сообщений в базе не равно количеству полученному из пакта, значит добавляем в базу новые
            if number_of_conversation_messages_db != number_of_conversation_messages:
                new_messages_for_database = get_new_messages_for_database(messages, conversation['external_id'])
                add_messages_to_database(new_messages_for_database, conversation['external_id'])
        else:
            add_conversation_to_database(conversation)
            add_messages_to_database(messages, conversation['external_id'])

    print('DB updated')

load_dotenv()

create_database()

update_data_base() # update conversations and messages in db
update_conversations_list() # update conversations in google sheets И заполнить дату анализа всем предыдущим днем(пустой первый прогон)
analyzing_main()

##upload_conversations_and_prompts # upload it to memory (or put it to db?) for next analysing
##analysing_conversations # analysing conversations from gs (take it from db) with prompts from gs (take it from db)