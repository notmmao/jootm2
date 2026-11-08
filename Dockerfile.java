# 旧 Java 11 + Netty 服务端（默认镜像已切到 Python）。
# 保留用于双栈协议对比：
#   docker build -f Dockerfile.java -t jootm2:java .
#   docker compose --profile java up jootm2-java --build
FROM maven:3.9-eclipse-temurin-11 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -q -B dependency:go-offline || true
COPY src ./src
RUN mvn -q -B -DskipTests package

FROM eclipse-temurin:11-jre
WORKDIR /app

COPY --from=build /build/target/jootm2.jar /app/jootm2.jar
COPY frontend/ /app/frontend/
COPY config/ /app/config/
# 旧镜像缺这份数据：CSV 模板 / Envir 脚本 / .map 都依赖它
COPY gamedata/ /app/gamedata/

RUN mkdir -p /app/data /app/resources

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/jootm2.jar", "/app/config/server.properties"]
