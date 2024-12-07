import sqlite3

def create_database():
    with sqlite3.connect('pact_database.sqlite') as pact_database:
        cursor = pact_database.cursor()

        cursor.execute(
            """CREATE TABLE IF NOT EXISTS 
                conversations (
                    external_id integer PRIMARY KEY, 
                    channel_id integer NOT NULL,
                    channel_type text NOT NULL,
                    name text NOT NULL,
                    sender_external_id text, 
                    created_at text NOT NULL,
                    created_at_timestamp integer NOT NULL,
                    contact_external_id text NOT NULL,
                    contact_external_public_id text,
                    contact_name text,
                    company_id integer NOT NULL,
                    chat_type text NOT NULL
                    )"""
        )

        #REFACTOR - channel_type УБРАТЬ ДЛЯ ТАБЛИЦЫ messages. А также убрать в процедуре заполнения этой таблицы
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS 
                messages (
                    external_id integer PRIMARY KEY,  
                    channel_id integer NOT NULL,
                    channel_type text NOT NULL,
                    message text,
                    income boolean NOT NULL,
                    created_at text NOT NULL,
                    created_at_timestamp integer NOT NULL,
                    attachment_external_id integer,
                    attachment_url text,
                    conversation_external_id integer NOT NULL
                    )"""
        )

        ### ЗАПРОС К НОВОЙ ТАБЛИЦЕ analysis (conversation_id, date_of_analysing, number_of_messages, text_result)
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS 
                analysis (
                    id integer PRIMARY KEY,
                    conversation_external_id integer NOT NULL, 
                    date_of_analysing text NOT NULL,
                    date_of_analysing_timestamp integer NOT NULL,
                    number_of_messages integer NOT NULL,
                    text_result text NOT NULL,
                    text_prompt text NOT NULL
                    
                    )"""
        )

        pact_database.commit()
