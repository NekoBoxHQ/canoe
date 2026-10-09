CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(64) NOT NULL, 
	detail TEXT NOT NULL, 
	ip VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_logs_action ON audit_logs (action);
CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE client_releases (
	id INTEGER NOT NULL, 
	version VARCHAR(32) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	notes TEXT NOT NULL, 
	min_version VARCHAR(32) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	published_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_client_releases_version ON client_releases (version);
CREATE INDEX ix_client_releases_enabled ON client_releases (enabled);

CREATE TABLE config_meta (
	"key" VARCHAR(64) NOT NULL, 
	value VARCHAR(255) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE nodes (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	remark VARCHAR(255) NOT NULL, 
	link TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_nodes_enabled ON nodes (enabled);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(32) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	expire_at DATETIME, 
	max_devices INTEGER NOT NULL, 
	remark VARCHAR(255) NOT NULL, 
	subscription TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_login_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE INDEX ix_users_status ON users (status);

CREATE TABLE sessions (
	id VARCHAR(32) NOT NULL, 
	user_id INTEGER NOT NULL, 
	node_id INTEGER, 
	device_id VARCHAR(64) NOT NULL, 
	last_seen DATETIME NOT NULL, 
	client_ip VARCHAR(64) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	expire_at DATETIME NOT NULL, 
	ended_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE CASCADE
);
CREATE INDEX ix_sessions_user_id ON sessions (user_id);
CREATE INDEX ix_sessions_last_seen ON sessions (last_seen);

CREATE TABLE tokens (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	device_id VARCHAR(64) NOT NULL, 
	sub_key VARCHAR(64) NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	expire_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_tokens_user_id ON tokens (user_id);
CREATE UNIQUE INDEX ix_tokens_token_hash ON tokens (token_hash);

CREATE TABLE user_node (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	node_id INTEGER NOT NULL, 
	assigned_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_node UNIQUE (user_id, node_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE CASCADE
);
CREATE INDEX ix_user_node_user_id ON user_node (user_id);
CREATE INDEX ix_user_node_node_id ON user_node (node_id);

