-- Initial Workflow ORM tables, frozen for deployment migration history.


CREATE TABLE idempotency_records (
	idempotency_key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (idempotency_key)
)

;


CREATE TABLE task_creation_order (
	id SERIAL NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (task_id)
)

;


CREATE TABLE tool_logs (
	id SERIAL NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	sequence INTEGER NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tool_logs_task_sequence UNIQUE (task_id, sequence)
)

;

CREATE INDEX ix_tool_logs_task_id ON tool_logs (task_id);


CREATE TABLE traces (
	id SERIAL NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	sequence INTEGER NOT NULL, 
	state_version INTEGER, 
	expected_state_version INTEGER, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_traces_task_sequence UNIQUE (task_id, sequence)
)

;

CREATE INDEX ix_traces_task_id ON traces (task_id);

CREATE UNIQUE INDEX uq_traces_task_state_version ON traces (task_id, state_version) WHERE state_version IS NOT NULL;


CREATE TABLE workflow_runs (
	task_id VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (task_id)
)

;
